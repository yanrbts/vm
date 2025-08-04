import os
# 数据库文件路径
DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'vm_manager.db')

# 基础虚拟机镜像路径 (QCOW2 格式)
# !!! 请替换为你的实际模板镜像路径 !!!
# 示例: 你可以下载一个 Ubuntu Cloud Image，例如 focal-server-cloudimg-amd64.img
# 然后通过 'qemu-img convert -f qcow2 -O qcow2 focal-server-cloudimg-amd64.img base_template.qcow2' 转换为 qcow2
BASE_IMAGE_PATH = "/var/lib/libvirt/images/kali-linux-2025.2-qemu-amd64.qcow2" 

