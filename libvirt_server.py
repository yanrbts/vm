# uvicorn libvirt_server:app --host 127.0.0.1 --port 5001 --workers 1
import os
import sys
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, status
from pydantic import BaseModel, Field
import uvicorn
import urllib3

from log import logger
from vm_libvirt_manager import LibvirtManager
from guacamodel import GuacamoleClient

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
load_dotenv()

# Pydantic model definition
# Used for request body data validation and documentation generation
class VMCreateRequest(BaseModel):
    vm_name: str = Field(..., min_length=1, description="Virtual Machine Name")
    vm_pwd: str = Field(..., min_length=3, description="Virtual Machine Password")
    memory_mb: int = Field(2048, gt=0, description="Memory in MiB")
    vcpu_count: int = Field(2, gt=0, description="Number of vCPUs")

class VMDelete(BaseModel):
    vm_name: str = Field(..., description="Virtual Machine Name")
    connid: int = Field(..., gt=0, description="Virtual Machine Connect ID")

class VMActionResponse(BaseModel):
    message: str
    success: bool
    data: dict | None = None

class VMDetails(BaseModel):
    name: str
    uuid: str
    status: str
    memory_mb: int
    vcpu_count: int
    vnc_port: int | None
    autostart: bool
    disk_path: str

class VMListResponse(BaseModel):
    vms: list[VMDetails]

GUAC_IP: str = os.getenv("GUAC_SERVER_IP", "192.168.3.132:8443")
VNC_IP: str = os.getenv("VNC_CLIENT_IP", "192.168.3.91")

# --- FastAPI application initialization ---
app = FastAPI(
    title="Libvirt VM Management API",
    description="RESTful API for managing virtual machines via Libvirt.",
    version="1.0.0",
)

# 初始化 LibvirtManager
# **重要提示：** LibvirtManager 的连接和权限（包括 sudo 免密配置）
# 应该在服务器启动前或内部处理好。这个服务器将以特定用户身份运行，
# 该用户在 /etc/sudoers 中配置了对 libvirt 和 qemu-img 命令的免密权限。
libvirt_manager = LibvirtManager()

@app.on_event("startup")
async def startup_event():
    """
    This event is executed when the application starts, used to check the connection status of LibvirtManager.
    """
    logger.info("Starting Libvirt VM Management API...")
    if not libvirt_manager.conn:
        logger.fatal("Libvirt connection failed on startup. API will not function correctly.")
    else:
        logger.info("Libvirt Manager connected successfully.")
        logger.info(f"Guacamole Server IP: {GUAC_IP}")
        logger.info(f"VNC Client IP (for VMs): {VNC_IP}")

@app.get("/api/v1/vms", response_model=VMListResponse, summary="List all Virtual Machines")
async def list_vms():
    """
    Lists all virtual machines defined or running in Libvirt and their detailed status.
    """
    logger.info("Received request to list VMs.")
    try:
        vms_info = libvirt_manager.list_vms()
        return VMListResponse(vms=vms_info)
    except Exception as e:
        logger.error(f"Error listing VMs: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to list VMs: {e}"
        )

@app.post("/api/v1/vms", response_model=VMActionResponse, status_code=status.HTTP_201_CREATED, summary="Create a new Virtual Machine")
async def create_vm(request_data: VMCreateRequest):
    """
    Creates a new virtual machine with the provided name, memory, and number of CPUs.
    """
    logger.info(f"Received request to create VM: {request_data.vm_name}")
    try:
        success, result = libvirt_manager.create_vm_from_template(
            request_data.vm_name, request_data.memory_mb, request_data.vcpu_count
        )
        if success:
            with GuacamoleClient(
                guac_hostname = GUAC_IP
            ) as guac:
                if isinstance(result, dict):
                    status, data = guac.grant_user_permissions(
                        username=request_data.vm_name, 
                        userpwd=request_data.vm_pwd, 
                        vnchost=VNC_IP,
                        vncport=result["vnc_port"]
                    )

                    if status:
                        logger.info(f"VM '{request_data.vm_name}': {data['link']}")
                        return VMActionResponse(
                            message=f"Virtual machine '{request_data.vm_name}' created and started successfully.",
                            success=True,
                            data=data
                        )
                    else:
                        return VMActionResponse(
                            message=f"Virtual machine '{data}' created failed",
                            success=False,
                            data=None
                        )
                else:
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail=f"Failed to create VM: {result}"
                    )
        else:
            logger.error(f"Failed to create VM '{request_data.vm_name}': {result}")
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Failed to create VM: {result}"
            )
    except Exception as e:
        logger.error(f"Unexpected error creating VM '{request_data.vm_name}': {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"An unexpected error occurred: {e}"
        )

@app.post("/api/v1/vms/{vm_name}/start", response_model=VMActionResponse, summary="Start a Virtual Machine")
async def start_vm(vm_name: str):
    """
    Starts a specified virtual machine.
    """
    logger.info(f"Received request to start VM: {vm_name}")
    success, message = libvirt_manager.start_vm(vm_name)
    if success:
        return VMActionResponse(message=message, success=True)
    else:
        logger.error(f"Failed to start VM '{vm_name}': {message}")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Failed to start VM: {message}"
        )

@app.post("/api/v1/vms/{vm_name}/stop", response_model=VMActionResponse, summary="Gracefully Stop a Virtual Machine")
async def stop_vm(vm_name: str):
    """
    Gracefully shut down a specified virtual machine (ACPI shutdown).
    """
    logger.info(f"Received request to stop VM: {vm_name}")
    success, message = libvirt_manager.stop_vm(vm_name)
    if success:
        return VMActionResponse(message=message, success=True)
    else:
        logger.error(f"Failed to stop VM '{vm_name}': {message}")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Failed to stop VM: {message}"
        )

@app.post("/api/v1/vms/{vm_name}/destroy", response_model=VMActionResponse, summary="Forcefully Power Off a Virtual Machine")
async def destroy_vm(vm_name: str):
    """
    Forcefully shut down a specified virtual machine.
    """
    logger.info(f"Received request to destroy VM: {vm_name}")
    success, message = libvirt_manager.destroy_vm(vm_name)
    if success:
        return VMActionResponse(message=message, success=True)
    else:
        logger.error(f"Failed to destroy VM '{vm_name}': {message}")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Failed to destroy VM: {message}"
        )

@app.delete("/api/v1/vms", response_model=VMActionResponse, summary="Delete a Virtual Machine")
async def delete_vm(request_data: VMDelete):
    """
    Deletes a specified virtual machine, including its disk files.
    """
    logger.info(f"Received request to delete VM: {request_data.vm_name}:{request_data.connid}")
    success, message = libvirt_manager.delete_vm(request_data.vm_name)
    if success:
        with GuacamoleClient(guac_hostname = GUAC_IP) as guac:
            status, msg = guac.delete_user_and_vm(name=request_data.vm_name, connid=request_data.connid)
        if status:
            return VMActionResponse(message=message, success=True)
        else:
            errmsg = f"Failed to delete Guacamole user/conn for VM '{request_data.vm_name}': {msg}"
            logger.error(errmsg)
            return VMActionResponse(message=errmsg, success=False)

    else:
        logger.error(f"Failed to delete VM '{request_data.vm_name}': {message}")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Failed to delete VM: {message}"
        )

@app.get("/api/v1/vms/{vm_name}/vnc_port", summary="Get VNC port of a Virtual Machine")
async def get_vm_vnc_port(vm_name: str):
    """
    Gets the VNC port of the specified virtual machine.
    """
    logger.info(f"Received request for VNC port of VM: {vm_name}")
    vnc_port = libvirt_manager.get_vm_vnc_port(vm_name)
    if vnc_port:
        return {"vm_name": vm_name, "vnc_port": vnc_port}
    else:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"VNC port not found for VM '{vm_name}' or VM does not exist/running."
        )


if __name__ == "__main__":
    # uvicorn libvirt_server:app --host 127.0.0.1 --port 5001 --workers 1
    uvicorn.run(app, host="127.0.0.1", port=5001)