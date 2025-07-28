from flask import Flask, render_template, request, redirect, url_for, flash
from vm_libvirt_manager import LibvirtManager
from database import init_db, add_vm_record, update_vm_status, delete_vm_record, get_all_vm_records, get_vm_record
from config import BASE_IMAGE_PATH, GUACAMOLE_BASE_URL
import os
import time

app = Flask(__name__)
app.secret_key = 'your_super_secret_key' # 替换为一个强密钥，用于 flash 消息

# 初始化数据库和 Libvirt 管理器
init_db()
vm_manager = LibvirtManager()

@app.route('/')
def index():
    """显示所有虚拟机列表"""
    vms_from_db = get_all_vm_records()
    
    # 刷新数据库中的虚拟机状态，与libvirt实际状态同步
    updated_vms = []
    for vm_record in vms_from_db:
        vm_name = vm_record[0]
        try:
            dom = vm_manager.conn.lookupByName(vm_name)
            current_status = vm_manager._get_domain_details(dom)['status']
            if current_status != vm_record[2]: # 如果状态不一致，更新数据库
                update_vm_status(vm_name, current_status)
                # 更新内存中的记录以反映最新状态
                vm_record_list = list(vm_record)
                vm_record_list[2] = current_status
                updated_vms.append(tuple(vm_record_list))
            else:
                updated_vms.append(vm_record)
        except libvirt.libvirtError:
            # 如果libvirt找不到该VM（可能已被手动删除），则从数据库中移除
            delete_vm_record(vm_name)
            flash(f"虚拟机 '{vm_name}' 在libvirt中未找到，已从数据库中移除。", 'warning')
    
    # 重新获取最新状态的虚拟机列表
    final_vms = get_all_vm_records()
    return render_template('index.html', vms=final_vms)

@app.route('/create_vm', methods=['GET', 'POST'])
def create_vm_page():
    """创建新虚拟机页面及处理逻辑"""
    if request.method == 'POST':
        vm_name = request.form['vm_name']
        memory_mb = int(request.form['memory_mb'])
        vcpu_count = int(request.form['vcpu_count'])

        if not vm_manager.conn:
            flash("Libvirt 连接失败，无法创建虚拟机。", 'error')
            return redirect(url_for('index'))

        # 检查基础镜像是否存在
        if not os.path.exists(BASE_IMAGE_PATH):
            flash(f"错误：基础镜像文件不存在: {BASE_IMAGE_PATH}", 'error')
            return redirect(url_for('create_vm_page'))

        success, result = vm_manager.create_vm_from_template(vm_name, memory_mb, vcpu_count)
        
        if success:
            vnc_port = result.get('vnc_port')
            add_vm_record(vm_name, os.path.basename(BASE_IMAGE_PATH), '运行中', vnc_port)
            flash(f"虚拟机 '{vm_name}' 创建并启动成功！VNC 端口: {vnc_port}", 'success')
            return redirect(url_for('index'))
        else:
            flash(f"创建虚拟机失败: {result}", 'error')
            return render_template('create_vm.html') # 失败时留在创建页面

    return render_template('create_vm.html')

@app.route('/manage_vm/<vm_name>', methods=['POST'])
def manage_vm_action(vm_name):
    """处理虚拟机操作 (启动、关机、删除等)"""
    action = request.form.get('action')

    if not vm_manager.conn:
        flash("Libvirt 连接失败，无法执行操作。", 'error')
        return redirect(url_for('index'))

    success = False
    message = ""
    
    if action == 'start':
        success, message = vm_manager.start_vm(vm_name)
        if success: update_vm_status(vm_name, '运行中')
    elif action == 'stop':
        success, message = vm_manager.stop_vm(vm_name)
        if success: update_vm_status(vm_name, '正在关机') # 状态可能短暂，最终会到'已关机'
        # 关机可能需要时间，这里可以加一个短暂等待或轮询检查
        time.sleep(5) 
        try: # 尝试更新到最终状态
            dom = vm_manager.conn.lookupByName(vm_name)
            final_status = vm_manager._get_domain_details(dom)['status']
            update_vm_status(vm_name, final_status)
        except libvirt.libvirtError:
            pass # VM可能已经消失
    elif action == 'destroy':
        success, message = vm_manager.destroy_vm(vm_name)
        if success: update_vm_status(vm_name, '已关机')
    elif action == 'delete':
        success, message = vm_manager.delete_vm(vm_name)
        if success: delete_vm_record(vm_name) # 从数据库中删除记录
    else:
        flash("无效的操作。", 'error')
        return redirect(url_for('index'))

    if success:
        flash(message, 'success')
    else:
        flash(message, 'error')

    return redirect(url_for('index'))

@app.route('/guacamole_link/<vm_name>')
def guacamole_link(vm_name):
    """生成 Guacamole 远程访问链接"""
    vm_record = get_vm_record(vm_name)
    if vm_record and vm_record[2] == '运行中' and vm_record[6]: # 状态为运行中且有VNC端口
        # 这里的连接名需要与你在 Guacamole 中配置的连接名一致
        # 简单示例，直接使用 VM 名称作为 Guacamole 连接名
        guacamole_connection_name = vm_name 
        full_guacamole_url = f"{GUACAMOLE_BASE_URL}{guacamole_connection_name}"
        flash(f"点击链接远程访问虚拟机 '{vm_name}'：<a href='{full_guacamole_url}' target='_blank'>{full_guacamole_url}</a>", 'success')
    else:
        flash(f"虚拟机 '{vm_name}' 未运行或 VNC 端口不可用，无法生成 Guacamole 链接。", 'error')
    
    return redirect(url_for('index'))


if __name__ == '__main__':
    # 确保数据库已初始化
    init_db()
    # 尝试连接 libvirt，如果失败会在控制台打印错误
    if not vm_manager.conn:
        print("请检查 libvirt 服务是否运行，以及当前用户是否有权限访问 libvirt。")
        print("例如：sudo systemctl start libvirtd && sudo systemctl enable libvirtd")
        print("并将当前用户添加到 libvirt 组：sudo usermod -a -G libvirt $(whoami)")
        print("然后重新登录或重启系统。")
    
    app.run(debug=True, host='0.0.0.0', port=5000)