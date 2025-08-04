import os, re
import uuid
import libvirt
import subprocess
from typing import Tuple, Union, Dict, List
import xml.etree.ElementTree as ET
import socket # For VNC port check

from log import logger
from config import LIBVIRT_URI, VM_STORAGE_POOL_PATH, BASE_IMAGE_PATH

class LibvirtManager:
    # Centralized map for system commands
    _SYSTEM_COMMANDS = {
        'systemctl_is_active': ['systemctl', 'is-active', 'libvirtd'],
        'systemctl_start': ['systemctl', 'start', 'libvirtd'],
        'systemctl_is_enabled': ['systemctl', 'is-enabled', 'libvirtd'],
        'systemctl_enable': ['systemctl', 'enable', 'libvirtd'],
        'groups_check': ['groups', '{user}'],
        'usermod_add_group': ['usermod', '-a', '-G', 'libvirt', '{user}'],
        'qemu_img_create': ['qemu-img', 'create', '-f', 'qcow2', '-b', '{base_image_path}', '-F', 'qcow2', '{new_disk_path}'],
        'chown': ['chown', '{owner_group}', '{path}'],
        'chmod': ['chmod', '{mode}', '{path}'],
        'rm_force': ['rm', '-f', '{path}'],
    }

    def __init__(self, uri=LIBVIRT_URI):
        self.uri = uri
        self.conn = None
        self._initial_setup() 

    def _initial_setup(self):
        """
        Performs initial Libvirt environment checks and attempts to fix common issues.
        This includes checking the libvirtd service and user group membership.
        """
        logger.info("Starting Libvirt environment initial setup and connection.")
        
        # 1. Check and fix libvirtd service
        service_ok = self._check_and_fix_libvirt_service()
        if not service_ok:
            logger.warning("Libvirtd service issues detected. Connection attempts might fail.")
        
        # 2. Check and add user to libvirt group
        self._check_and_add_user_to_libvirt_group()

        # 3. Attempt to connect to Libvirt
        self._connect()
        
        if not self.conn:
            logger.error("!!! FATAL: Libvirt connection remains unsuccessful after initial setup attempts. !!!")
            logger.error("!!! Please ensure libvirt is fully installed, its service is running, "
                         "and the current user has correct permissions. !!!")
            logger.error("!!! For example, try: 'sudo systemctl status libvirtd' and 'groups $(whoami)'. "
                         "Restarting your system may be required for group changes to take effect. !!!")
        else:
            logger.info("Libvirt environment checks completed, connection established successfully.")


    def _connect(self):
        """Attempts to connect to libvirt."""
        if self.conn and self.conn.isAlive():
            logger.debug("Already connected to libvirt.")
            return

        try:
            self.conn = libvirt.open(self.uri)
            if self.conn is None:
                raise Exception(f'Failed to connect to libvirt URI: {self.uri}')
            logger.info(f"Successfully connected to libvirt: {self.uri}")
        except libvirt.libvirtError as e:
            logger.error(f"Failed to connect to libvirt: {e}")
            self.conn = None # Ensure connection is None on failure

    def _reconnect(self):
        """Attempts to reconnect if the connection is lost."""
        if not self.conn or not self.conn.isAlive():
            logger.warning("Libvirt connection lost or not established, attempting to reconnect...")
            self._connect()
        return self.conn

    @staticmethod
    def _run_system_command_sudo(command_template_key, **kwargs):
        """
        Executes a system command with sudo permissions using a predefined template.
        Args:
            command_template_key (str): Key from _SYSTEM_COMMANDS identifying the command.
            **kwargs: Arguments to format the command template (e.g., user='username').
        Returns:
            tuple: (bool success, str output/error_message)
        """
        if command_template_key not in LibvirtManager._SYSTEM_COMMANDS:
            logger.error(f"Error: Command template '{command_template_key}' not found in _SYSTEM_COMMANDS.")
            return False, f"Command template '{command_template_key}' not found."

        command_args = LibvirtManager._SYSTEM_COMMANDS[command_template_key]
        
        # Format the command arguments using kwargs
        # Ensure all args are strings before formatting
        formatted_command_args = [arg.format(**kwargs) if '{' in arg and '}' in arg else arg for arg in command_args]
        
        logger.info(f"Executing system command (might require sudo password): {' '.join(formatted_command_args)}")
        try:
            process = subprocess.Popen(
                ["sudo"] + formatted_command_args,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True # Use text mode for string handling
            )
            stdout, stderr = process.communicate()
            
            if process.returncode == 0:
                logger.info(f"Command successful: {' '.join(formatted_command_args)}")
                if stdout:
                    logger.info(f"stdout: {stdout.strip()}")
                return True, stdout.strip()
            else:
                logger.error(f"Command failed: {' '.join(formatted_command_args)}")
                logger.error(f"stderr: {stderr.strip()}")
                return False, stderr.strip()
        except FileNotFoundError:
            logger.error(f"Error: Command '{formatted_command_args[0]}' not found. Please ensure it is installed and in your PATH.")
            return False, f"Command '{formatted_command_args[0]}' not found."
        except Exception as e:
            logger.error(f"An exception occurred while executing command: {e}")
            return False, str(e)

    def _check_and_fix_libvirt_service(self):
        """Checks and attempts to fix the libvirtd service status."""
        logger.info("Checking libvirtd service status...")
        
        # Check if service is active
        success, output = self._run_system_command_sudo('systemctl_is_active')
        if success and output == 'active':
            logger.info("libvirtd service is running.")
        else:
            logger.info("libvirtd service is not running or inactive, attempting to start...")
            success, message = self._run_system_command_sudo('systemctl_start')
            if success:
                logger.info("libvirtd service successfully started.")
            else:
                logger.warning(f"Warning: Failed to start libvirtd service: {message}")
                logger.warning("Please manually check service status: sudo systemctl status libvirtd")
                return False

        # Check if service is enabled (autostart on boot)
        success, output = self._run_system_command_sudo('systemctl_is_enabled') # Assuming this is a typo in original, should be is-enabled
        if success and output == 'enabled':
            logger.info("libvirtd service is set to autostart.")
        else:
            logger.info("libvirtd service is not set to autostart, attempting to enable...")
            success, message = self._run_system_command_sudo('systemctl_enable')
            if success:
                logger.info("libvirtd service successfully enabled for autostart.")
            else:
                logger.warning(f"Warning: Failed to enable libvirtd service autostart: {message}")
        return True

    def _check_and_add_user_to_libvirt_group(self):
        """Checks if the current user is in the libvirt group and adds them if not."""
        current_user = os.getenv('USER') # Get current username
        if not current_user:
            logger.warning("Warning: Could not get current username, skipping user group check.")
            return

        logger.info(f"Checking if user '{current_user}' is in 'libvirt' group...")
        
        # Check user's groups
        success, output = self._run_system_command_sudo('groups_check', user=current_user)
        
        if success and 'libvirt' in output.split():
            logger.info(f"User '{current_user}' is already in the 'libvirt' group.")
        else:
            logger.info(f"User '{current_user}' is not in 'libvirt' group, attempting to add...")
            success, message = self._run_system_command_sudo('usermod_add_group', user=current_user)
            if success:
                logger.info(f"User '{current_user}' successfully added to 'libvirt' group.")
                logger.info("!!! IMPORTANT: Please log out and log back in (or reboot) for group permissions to take effect. !!!")
            else:
                logger.warning(f"Warning: Failed to add user '{current_user}' to 'libvirt' group: {message}")
                logger.warning("Please manually execute: sudo usermod -a -G libvirt $(whoami) and re-login.")

    def list_vms(self) -> List:
        """Lists all virtual machines and their statuses."""
        conn = self._reconnect()
        if not conn: 
            return []

        vms_info = []
        try:
            domain_ids = conn.listDomainsID()
            for dom_id in domain_ids:
                dom = conn.lookupByID(dom_id)
                vms_info.append(self._get_domain_details(dom))

            inactive_domains = conn.listDefinedDomains()
            for dom_name in inactive_domains:
                dom = conn.lookupByName(dom_name)
                # Avoid adding duplicates if already listed from active domains
                if not any(vm['name'] == dom_name for vm in vms_info):
                    vms_info.append(self._get_domain_details(dom))
        except libvirt.libvirtError as e:
            logger.error(f"Failed to list virtual machines: {e}")
        return vms_info

    def _get_domain_details(self, dom) -> Dict:
        """Retrieves detailed information for a single domain."""
        if not dom: return {}
        
        info = dom.info()
        status_map = {
            libvirt.VIR_DOMAIN_NOSTATE: 'No State',
            libvirt.VIR_DOMAIN_RUNNING: 'Running',
            libvirt.VIR_DOMAIN_BLOCKED: 'Blocked',
            libvirt.VIR_DOMAIN_PAUSED: 'Paused',
            libvirt.VIR_DOMAIN_SHUTDOWN: 'Shutting Down',
            libvirt.VIR_DOMAIN_SHUTOFF: 'Shut Off',
            libvirt.VIR_DOMAIN_CRASHED: 'Crashed',
            libvirt.VIR_DOMAIN_PMSUSPENDED: 'Suspended',
        }
        status = status_map.get(info[0], 'Unknown State')
        
        # Attempt to get VNC port
        vnc_port = self._get_vnc_port(dom.XMLDesc(0))

        return {
            'name': dom.name(),
            'uuid': dom.UUIDString(),
            'status': status,
            'memory_mb': info[1],
            'vcpu_count': info[3],
            'vnc_port': vnc_port,
            'autostart': dom.autostart() == 1,
            'disk_path': self._get_disk_path(dom.XMLDesc(0))
        }

    def _get_disk_path(self, xml_desc):
        """Extracts disk file path from libvirt domain XML."""
        root = ET.fromstring(xml_desc)
        for disk in root.findall(".//devices/disk"):
            target = disk.find("target")
            if target is not None and target.get('dev') == 'vda':  # 主磁盘
                source = disk.find("source")
                if source is not None:
                    return source.get('file')
        return "Unknown"

    def _get_vnc_port(self, xml_desc):
        """Extracts VNC port from XML description."""
        root = ET.fromstring(xml_desc)
        graphics = root.find(".//graphics[@type='vnc']")
        if graphics is not None:
            port = graphics.get('port')
            if port and port != '-1': # -1 means dynamic allocation
                return int(port)
        return None # Not found or dynamically allocated

    def create_vm_from_template(self, vm_name, memory_mb=2048, vcpu_count=2) -> Tuple[bool, Union[str, Dict]]:
        """
        Clones from a base image and creates a new virtual machine.
        This is a fast cloning implementation by copying the QCOW2 image and generating new XML.
        """
        conn = self._reconnect()
        if not conn: 
            return False, "❌ Libvirt connection failed"

        if not os.path.exists(BASE_IMAGE_PATH):
            return False, f"❌ Base image file does not exist: {BASE_IMAGE_PATH}"

        # Check if VM name already exists
        try:
            conn.lookupByName(vm_name)
            return False, f"Virtual machine '{vm_name}' already exists"
        except libvirt.libvirtError:
            pass # VM does not exist, can create

        # 1. Clone disk image
        new_disk_path = os.path.join(VM_STORAGE_POOL_PATH, f"{vm_name}.qcow2")
        if os.path.exists(new_disk_path):
            success, msg = self._run_system_command_sudo('rm_force', path=new_disk_path)
            if not success:
                return False, f"❌ Failed to remove existing disk image: {msg}"
            logger.info(f"Successfully removed existing disk image: {new_disk_path}")

        success, msg = self._run_system_command_sudo(
            'qemu_img_create', 
            base_image_path=BASE_IMAGE_PATH, 
            new_disk_path=new_disk_path
        )
        if not success:
            return False, f"❌ Failed to clone disk image: {msg}"
        logger.info(f"Successfully cloned disk image to: {new_disk_path}")

        # Set ownership and permissions for the new disk image
        success, msg = self._run_system_command_sudo('chown', owner_group='root:libvirt', path=new_disk_path)
        if not success:
            return False, f"❌ Failed to set disk image ownership: {msg}"
        success, msg = self._run_system_command_sudo('chmod', mode='g+rw', path=new_disk_path)
        if not success:
            return False, f"❌ Failed to set disk image permissions: {msg}"
        logger.info(f"Set permissions for new disk image: {new_disk_path}")

        # 2. Generate VM XML configuration
        vm_uuid = str(uuid.uuid4())
        
        # Find an available VNC port
        vnc_port = 5900
        while True:
            try:
                with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                    s.settimeout(0.1)
                    s.connect(('127.0.0.1', vnc_port))
                vnc_port += 1
            except (socket.timeout, ConnectionRefusedError):
                break # Port is available

        xml_config = f"""
        <domain type='kvm'>
          <name>{vm_name}</name>
          <uuid>{vm_uuid}</uuid>
          <memory unit='MiB'>{memory_mb}</memory>
          <currentMemory unit='MiB'>{memory_mb}</currentMemory>
          <vcpu placement='static'>{vcpu_count}</vcpu>
          <os>
            <type arch='x86_64' machine='pc-q35-6.2'>hvm</type>
            <boot dev='hd'/>
          </os>
          <features>
            <acpi/>
            <apic/>
            <vmport state='off'/>
          </features>
          <cpu mode='host-passthrough' check='none' migratable='on'/>
          <clock offset='utc'/>
          <on_poweroff>destroy</on_poweroff>
          <on_reboot>restart</on_reboot>
          <on_crash>destroy</on_crash>
          <devices>
            <emulator>/usr/bin/qemu-system-x86_64</emulator>
            <disk type='file' device='disk'>
              <driver name='qemu' type='qcow2'/>
              <source file='{new_disk_path}'/>
              <target dev='vda' bus='virtio'/>
              <address type='pci' domain='0x0000' bus='0x04' slot='0x00' function='0x0'/>
            </disk>
            <controller type='usb' index='0' model='qemu-xhci' ports='15'>
              <address type='pci' domain='0x0000' bus='0x02' slot='0x00' function='0x0'/>
            </controller>
            <controller type='pci' index='0' model='pcie-root'/>
            <controller type='pci' index='1' model='pcie-root-port'>
              <model name='pcie-root-port'/>
              <target chassis='1' port='0x8'/>
              <address type='pci' domain='0x0000' bus='0x00' slot='0x01' function='0x0'/>
            </controller>
            <controller type='pci' index='2' model='pcie-root-port'>
              <model name='pcie-root-port'/>
              <target chassis='2' port='0x9'/>
              <address type='pci' domain='0x0000' bus='0x00' slot='0x01' function='0x1'/>
            </controller>
            <controller type='pci' index='3' model='pcie-root-port'>
              <model name='pcie-root-port'/>
              <target chassis='3' port='0xa'/>
              <address type='pci' domain='0x0000' bus='0x00' slot='0x01' function='0x2'/>
            </controller>
            <controller type='pci' index='4' model='pcie-root-port'>
              <model name='pcie-root-port'/>
              <target chassis='4' port='0xb'/>
              <address type='pci' domain='0x0000' bus='0x00' slot='0x01' function='0x3'/>
            </controller>
            <controller type='pci' index='5' model='pcie-root-port'>
              <model name='pcie-root-port'/>
              <target chassis='5' port='0xc'/>
              <address type='pci' domain='0x0000' bus='0x00' slot='0x01' function='0x4'/>
            </controller>
            <controller type='pci' index='6' model='pcie-root-port'>
              <model name='pcie-root-port'/>
              <target chassis='6' port='0xd'/>
              <address type='pci' domain='0x0000' bus='0x00' slot='0x01' function='0x5'/>
            </controller>
            <interface type='network'>
              <source network='default'/>
              <model type='virtio'/>
              <address type='pci' domain='0x0000' bus='0x03' slot='0x00' function='0x0'/>
            </interface>
            <console type='pty'>
              <target type='serial' port='0'/>
            </console>
            <serial type='pty'>
                <target port='0'/>
            </serial>
            <channel type='unix'>
              <target type='virtio' name='org.qemu.guest_agent.0'/>
              <address type='virtio-serial' controller='0' bus='0' port='1'/>
            </channel>
            <input type='tablet' bus='usb'/>
            <input type='keyboard' bus='ps2'/>
            <graphics type='vnc' port='{vnc_port}' autoport='no' listen='0.0.0.0'>
              <listen type='address' address='0.0.0.0'/>
            </graphics>
            <video>
              <model type='qxl' vram='65536' primary='yes'/>
              <address type='pci' domain='0x0000' bus='0x01' slot='0x00' function='0x0'/>
            </video>
            <memballoon model='virtio'>
              <address type='pci' domain='0x0000' bus='0x05' slot='0x00' function='0x0'/>
            </memballoon>
          </devices>
          <seclabel type='dynamic' model='dac' relabel='yes'/>
        </domain>
        """
        try:
            dom = conn.defineXML(xml_config)
            if dom is None:
                return False, "❌ Failed to define virtual machine"
            
            dom.create() # Start the virtual machine
            logger.info(f"✅ Virtual machine '{vm_name}' created and started successfully.")
            return True, {"name": vm_name, "vnc_port": vnc_port}
        except libvirt.libvirtError as e:
            # Clean up potentially created disk file if VM definition/start fails
            if os.path.exists(new_disk_path):
                success, cleanup_msg = self._run_system_command_sudo('rm_force', path=new_disk_path)
                if success:
                    logger.error(f"Cleaned up disk image after VM creation failure: {new_disk_path}")
                else:
                    logger.error(f"Warning: Failed to clean up disk image '{new_disk_path}' after VM creation error: {cleanup_msg}")
            return False, f"❌ Failed to create virtual machine: {e}"

    def start_vm(self, vm_name) -> Tuple[bool, Union[str]]:
        """Starts a virtual machine."""
        conn = self._reconnect()
        if not conn: 
            return False, "❌ Libvirt connection failed"
        try:
            dom = conn.lookupByName(vm_name)
            dom.create()
            logger.info(f"Virtual machine '{vm_name}' started successfully.")
            return True, f"✅ Virtual machine '{vm_name}' started successfully."
        except libvirt.libvirtError as e:
            return False, f"❌ Failed to start virtual machine '{vm_name}': {e}"

    def stop_vm(self, vm_name) -> Tuple[bool, Union[str]]:
        """Gracefully shuts down a virtual machine (ACPI shutdown)."""
        conn = self._reconnect()
        if not conn: 
            return False, "❌ Libvirt connection failed"
        
        try:
            dom = conn.lookupByName(vm_name)
            dom.shutdown()
            logger.info(f"Virtual machine '{vm_name}' is shutting down.")
            return True, f"✅ Virtual machine '{vm_name}' is shutting down."
        except libvirt.libvirtError as e:
            return False, f"❌ Failed to shut down virtual machine '{vm_name}': {e}"

    def destroy_vm(self, vm_name) -> Tuple[bool, Union[str]]:
        """Forcefully powers off a virtual machine."""
        conn = self._reconnect()
        if not conn: 
            return False, "Libvirt connection failed"

        try:
            dom = conn.lookupByName(vm_name)
            dom.destroy()
            logger.info(f"Virtual machine '{vm_name}' forcefully powered off successfully.")
            return True, f"✅ Virtual machine '{vm_name}' forcefully powered off successfully."
        except libvirt.libvirtError as e:
            return False, f"❌ Failed to forcefully power off virtual machine '{vm_name}': {e}"

    def delete_vm(self, vm_name) -> Tuple[bool, Union[str]]:
        """Deletes a virtual machine (including its disk file)."""
        conn = self._reconnect()
        if not conn: 
            return False, "Libvirt connection failed"
        try:
            dom = conn.lookupByName(vm_name)
            if dom.isActive():
                dom.destroy() # First, forcefully power off
                logger.info(f"Virtual machine '{vm_name}' has been forcefully powered off.")
            
            # Get disk path
            disk_path = self._get_disk_path(dom.XMLDesc(0))
            
            dom.undefine() # Undefine the virtual machine
            logger.info(f"Virtual machine '{vm_name}' has been undefined.")

            # Delete disk file
            if disk_path and os.path.exists(disk_path):
                success, msg = self._run_system_command_sudo('rm_force', path=disk_path)
                if not success:
                    return False, f"❌ Failed to delete disk file '{disk_path}': {msg}"
                logger.info(f"Virtual machine disk file '{disk_path}' deleted.")
            
            return True, f"✅ Virtual machine '{vm_name}' deleted successfully."
        except libvirt.libvirtError as e:
            return False, f"❌ Failed to delete virtual machine '{vm_name}': {e}"
        except Exception as e:
            return False, f"❌ An unexpected error occurred while deleting virtual machine '{vm_name}': {e}"

    def get_vm_vnc_port(self, vm_name):
        """Gets the VNC port of a virtual machine."""
        conn = self._reconnect()
        if not conn: 
            return None
        try:
            dom = conn.lookupByName(vm_name)
            return self._get_vnc_port(dom.XMLDesc(0))
        except libvirt.libvirtError:
            return None # VM does not exist or is not running
    
    def get_domain_by_name(self, vm_name):
        """
        Looks up a libvirt domain by its name.
        Handles connection and potential libvirt errors internally.
        
        Args:
            vm_name (str): The name of the virtual machine.
        
        Returns:
            libvirt.virDomain or None: The domain object if found, otherwise None.
        """
        conn = self._reconnect()
        if not conn:
            logger.error(f"Cannot lookup VM '{vm_name}': Libvirt connection failed.")
            return None
        try:
            dom = conn.lookupByName(vm_name)
            return dom
        except libvirt.libvirtError as e:
            # Typically means domain not found, but could be other libvirt errors
            logger.warning(f"Failed to lookup domain '{vm_name}': {e}")
            return None
        
    def delete_vm_file(vm_name: str):
        """
        自动关闭、注销并删除虚拟机及其.qcow2磁盘文件
        :param vm_name: 虚拟机名称
        """
        def run(cmd):
            result = subprocess.run(cmd, shell=True, capture_output=True, text=True)
            return result.returncode, result.stdout.strip(), result.stderr.strip()

        logger.info(f"🔍 尝试删除虚拟机: {vm_name}")

        # # Step 1: 尝试关闭虚拟机
        # print("➡️ 正在尝试关闭虚拟机...")
        # run(f"sudo virsh destroy {vm_name}")  # destroy 即便关闭失败也不会阻止后续步骤

        # Step 2: 获取磁盘路径
        logger.info("➡️ 获取磁盘路径...")
        ret, output, err = run(f"sudo virsh domblklist {vm_name}")
        if ret != 0:
            logger.error(f"❌ 获取磁盘路径失败: {err}")
            return

        disk_paths = []
        for line in output.splitlines():
            if line.startswith("vda") or line.startswith("hda") or line.startswith("sda"):
                parts = line.split()
                if len(parts) == 2:
                    disk_paths.append(parts[1])

        # Step 3: 注销虚拟机
        logger.info("➡️ 注销虚拟机定义...")
        ret, _, err = run(f"sudo virsh undefine {vm_name}")
        if ret != 0:
            logger.error(f"❌ 注销失败: {err}")
            return

        # Step 4: 删除磁盘文件
        for disk_path in disk_paths:
            if os.path.exists(disk_path):
                logger.info(f"🗑️ 删除磁盘文件: {disk_path}")
                try:
                    os.remove(disk_path)
                except Exception as e:
                    logger.error(f"❌ 删除失败: {e}")
            else:
                logger.info(f"⚠️ 磁盘文件不存在: {disk_path}")

        logger.info(f"✅ 虚拟机 {vm_name} 删除完成。")