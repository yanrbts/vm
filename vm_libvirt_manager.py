import libvirt
import os
import uuid
import xml.etree.ElementTree as ET
import subprocess # Ensure subprocess is imported for running shell commands
from config import LIBVIRT_URI, VM_STORAGE_POOL_PATH, BASE_IMAGE_PATH

class LibvirtManager:
    def __init__(self, uri=LIBVIRT_URI):
        self.uri = uri
        self.conn = None
        self._connect()

    def _connect(self):
        """Connects to libvirt."""
        try:
            self.conn = libvirt.open(self.uri)
            if self.conn is None:
                raise Exception(f'Failed to connect to libvirt URI: {self.uri}')
            print(f"Successfully connected to libvirt: {self.uri}")
        except libvirt.libvirtError as e:
            print(f"Failed to connect to libvirt: {e}")
            self.conn = None # Ensure connection is None on failure

    def _reconnect(self):
        """Attempts to reconnect if the connection is lost."""
        if not self.conn or not self.conn.isAlive():
            print("Libvirt connection lost, attempting to reconnect...")
            self._connect()
        return self.conn

    def list_vms(self):
        """Lists all virtual machines and their statuses."""
        conn = self._reconnect()
        if not conn: return []

        vms_info = []
        try:
            domain_ids = conn.listDomainsID()
            for dom_id in domain_ids:
                dom = conn.lookupByID(dom_id)
                vms_info.append(self._get_domain_details(dom))

            inactive_domains = conn.listDefinedDomains()
            for dom_name in inactive_domains:
                dom = conn.lookupByName(dom_name)
                vms_info.append(self._get_domain_details(dom))
        except libvirt.libvirtError as e:
            print(f"Failed to list virtual machines: {e}")
        return vms_info

    def _get_domain_details(self, dom):
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
        """Extracts disk path from XML description."""
        root = ET.fromstring(xml_desc)
        for target in root.findall(".//disk/target"):
            if target.get('dev') == 'vda': # Assuming main disk is vda
                source = target.find("../source")
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

    def create_vm_from_template(self, vm_name, memory_mb=2048, vcpu_count=2):
        """
        Clones from a base image and creates a new virtual machine.
        This is a fast cloning implementation by copying the QCOW2 image and generating new XML.
        """
        conn = self._reconnect()
        if not conn: return False, "Libvirt connection failed"

        if not os.path.exists(BASE_IMAGE_PATH):
            return False, f"Base image file does not exist: {BASE_IMAGE_PATH}"

        # Check if VM name already exists
        try:
            conn.lookupByName(vm_name)
            return False, f"Virtual machine '{vm_name}' already exists"
        except libvirt.libvirtError:
            pass # VM does not exist, can create

        # 1. Clone disk image
        new_disk_path = os.path.join(VM_STORAGE_POOL_PATH, f"{vm_name}.qcow2")
        if os.path.exists(new_disk_path):
            try:
                # Attempt to remove existing file, handling permission errors
                subprocess.run(['sudo', 'rm', '-f', new_disk_path], check=True)
                print(f"Successfully removed existing disk image: {new_disk_path}")
            except subprocess.CalledProcessError as e:
                return False, f"Failed to remove existing disk image due to permission error: {e}. Please check permissions for '{new_disk_path}'."
            except Exception as e:
                return False, f"An unexpected error occurred while removing existing disk image: {e}"

        try:
            # Use qemu-img command for fast cloning (COW - Copy On Write)
            # This is much faster than direct file copy as it only copies metadata; actual data is copied on write.
            # Ensure qemu-img command is available.
            subprocess.run(['sudo', 'qemu-img', 'create', '-f', 'qcow2', '-b', BASE_IMAGE_PATH, '-F', 'qcow2', new_disk_path], check=True)
            print(f"Successfully cloned disk image to: {new_disk_path}")

            # --- FIX: Explicitly set ownership and permissions for the new disk image ---
            # This ensures the file is owned by root:libvirt and group-writable,
            # allowing the current user (if in libvirt group) to delete it later.
            subprocess.run(['sudo', 'chown', f'root:libvirt', new_disk_path], check=True)
            subprocess.run(['sudo', 'chmod', 'g+rw', new_disk_path], check=True)
            print(f"Set permissions for new disk image: {new_disk_path}")

        except subprocess.CalledProcessError as e:
            return False, f"Failed to clone disk image or set permissions: {e}"
        except Exception as e:
            return False, f"An unexpected error occurred during disk cloning or permission setting: {e}"

        # 2. Generate VM XML configuration
        vm_uuid = str(uuid.uuid4())
        
        # Find an available VNC port (usually starting from 5900)
        # This is a simplified search; a production environment should be more robust.
        vnc_port = 5900
        while True:
            try:
                # Attempt to connect to the port; if it fails, the port is likely available.
                # This is a simplified check; more accurate would be to query libvirt for allocated ports.
                # But for a simple example, this can work.
                import socket
                with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                    s.settimeout(0.1)
                    s.connect(('127.0.0.1', vnc_port))
                vnc_port += 1
            except (socket.timeout, ConnectionRefusedError):
                break # Port is available

        # --- FIX: Changed machine type to pc-q35-6.2 for broader compatibility ---
        # If this still fails, try 'pc-q35-5.2' or 'pc-i440fx-6.2'
        # To find supported types, run: /usr/bin/qemu-system-x86_64 -M help | grep q35
        # Or: /usr/bin/qemu-system-x86_64 -M help | grep pc
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
              <target type='virtio' port='0'/>
            </console>
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
                return False, "Failed to define virtual machine"
            
            dom.create() # Start the virtual machine
            print(f"Virtual machine '{vm_name}' created and started successfully.")
            return True, {"name": vm_name, "vnc_port": vnc_port}
        except libvirt.libvirtError as e:
            # Clean up potentially created disk file
            if os.path.exists(new_disk_path):
                try:
                    # Use sudo rm to ensure deletion even if permissions are tricky
                    subprocess.run(['sudo', 'rm', '-f', new_disk_path], check=True)
                    print(f"Cleaned up disk image after VM creation failure: {new_disk_path}")
                except subprocess.CalledProcessError as cleanup_e:
                    print(f"Warning: Failed to clean up disk image '{new_disk_path}' after VM creation error: {cleanup_e}")
            return False, f"Failed to create virtual machine: {e}"

    def start_vm(self, vm_name):
        """Starts a virtual machine."""
        conn = self._reconnect()
        if not conn: return False, "Libvirt connection failed"
        try:
            dom = conn.lookupByName(vm_name)
            dom.create()
            print(f"Virtual machine '{vm_name}' started successfully.")
            return True, f"Virtual machine '{vm_name}' started successfully."
        except libvirt.libvirtError as e:
            return False, f"Failed to start virtual machine '{vm_name}': {e}"

    def stop_vm(self, vm_name):
        """Gracefully shuts down a virtual machine (ACPI shutdown)."""
        conn = self._reconnect()
        if not conn: return False, "Libvirt connection failed"
        try:
            dom = conn.lookupByName(vm_name)
            dom.shutdown()
            print(f"Virtual machine '{vm_name}' is shutting down.")
            return True, f"Virtual machine '{vm_name}' is shutting down."
        except libvirt.libvirtError as e:
            return False, f"Failed to shut down virtual machine '{vm_name}': {e}"

    def destroy_vm(self, vm_name):
        """Forcefully powers off a virtual machine."""
        conn = self._reconnect()
        if not conn: return False, "Libvirt connection failed"
        try:
            dom = conn.lookupByName(vm_name)
            dom.destroy()
            print(f"Virtual machine '{vm_name}' forcefully powered off successfully.")
            return True, f"Virtual machine '{vm_name}' forcefully powered off successfully."
        except libvirt.libvirtError as e:
            return False, f"Failed to forcefully power off virtual machine '{vm_name}': {e}"

    def delete_vm(self, vm_name):
        """Deletes a virtual machine (including its disk file)."""
        conn = self._reconnect()
        if not conn: return False, "Libvirt connection failed"
        try:
            dom = conn.lookupByName(vm_name)
            if dom.isActive():
                dom.destroy() # First, forcefully power off
                print(f"Virtual machine '{vm_name}' has been forcefully powered off.")
            
            # Get disk path
            disk_path = self._get_disk_path(dom.XMLDesc(0))
            
            dom.undefine() # Undefine the virtual machine
            print(f"Virtual machine '{vm_name}' has been undefined.")

            # Delete disk file
            if disk_path and os.path.exists(disk_path):
                try:
                    # Use sudo rm to ensure deletion even if permissions are tricky
                    subprocess.run(['sudo', 'rm', '-f', disk_path], check=True)
                    print(f"Virtual machine disk file '{disk_path}' deleted.")
                except subprocess.CalledProcessError as e:
                    print(f"Error: Failed to delete disk file '{disk_path}' due to permission error: {e}")
                    return False, f"Failed to delete disk file '{disk_path}': {e}"
            
            return True, f"Virtual machine '{vm_name}' deleted successfully."
        except libvirt.libvirtError as e:
            return False, f"Failed to delete virtual machine '{vm_name}': {e}"
        except Exception as e:
            return False, f"An unexpected error occurred while deleting virtual machine '{vm_name}': {e}"

    def get_vm_vnc_port(self, vm_name):
        """Gets the VNC port of a virtual machine."""
        conn = self._reconnect()
        if not conn: return None
        try:
            dom = conn.lookupByName(vm_name)
            return self._get_vnc_port(dom.XMLDesc(0))
        except libvirt.libvirtError:
            return None # VM does not exist or is not running
