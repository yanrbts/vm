# 命令
## 安装
```bash
sudo apt install qemu-kvm libvirt-daemon-system libvirt-clients bridge-utils virtinst virt-manager virt-viewer
sudo systemctl enable libvirtd
sudo systemctl start libvirtd
sudo systemctl status libvirtd

sudo virsh net-start default
sudo virsh net-autostart default

sudo usermod -aG libvirt $USER
newgrp libvirt
```
## 安装
```bash
sudo cp /home/yrb/kali-linux-2023.3-installer-amd64.iso ./
sudo qemu-img create -f qcow2 kali.qcow2 100G

sudo virt-install \
  --name kali \
  --ram 4096 \
  --vcpus 2 \
  --disk path=/var/lib/libvirt/images/kali.qcow2,format=qcow2 \
  --cdrom /var/lib/libvirt/images/kali-linux-2023.3-installer-amd64.iso \
  --os-variant debian11 \
  --network network=default \
  --graphics vnc


virt-install \
  --name kali \
  --ram 4096 \
  --vcpus 2 \
  --disk path=/var/lib/libvirt/images/kali-linux-2025.2-qemu-amd64.qcow2,format=qcow2 \
  --import \
  --os-variant debian11 \
  --network network=default \
  --graphics vnc
```

## 部署
```bash
sudo apt install -y pkg-config libvirt-dev python3-dev
```