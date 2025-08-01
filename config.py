import os

# Libvirt 连接 URI
# qemu:///system 表示连接到系统级的 QEMU/KVM
LIBVIRT_URI = "qemu:///system"

# 基础虚拟机镜像路径 (QCOW2 格式)
# !!! 请替换为你的实际模板镜像路径 !!!
# 示例: 你可以下载一个 Ubuntu Cloud Image，例如 focal-server-cloudimg-amd64.img
# 然后通过 'qemu-img convert -f qcow2 -O qcow2 focal-server-cloudimg-amd64.img base_template.qcow2' 转换为 qcow2
BASE_IMAGE_PATH = "/var/lib/libvirt/images/kali-linux-2025.2-qemu-amd64.qcow2" 

# 虚拟机磁盘存储池路径
# 这是 libvirt 默认存储虚拟机磁盘镜像的路径
VM_STORAGE_POOL_PATH = "/var/lib/libvirt/images"

# 数据库文件路径
DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'vm_manager.db')

# Guacamole 访问地址 (示例，你需要实际部署 Guacamole)
# 假设 Guacamole 服务器运行在 192.168.1.100，并且你的 Guacamole 连接名为 'my_vm_connection'
# 这个连接名需要在 Guacamole 中为每个虚拟机手动或通过 Guacamole API 配置
GUACAMOLE_BASE_URL = "http://192.168.3.132:8443/#/client/"

GUACAMOLE_API_BASE_URL = "http://192.168.3.132:8443/api"
GUACAMOLE_ADMIN_USERNAME = "guacadmin" # 强烈建议在Guacamole中创建专用的API用户，并赋予最小权限
GUACAMOLE_ADMIN_PASSWORD = "guacadmin" # 强烈建议修改默认密码，并为API用户设置强密码
GUACAMOLE_WEB_URL = "http://192.168.3.132:8443/" # Guacamole Web UI 的根URL，确保末尾有斜杠