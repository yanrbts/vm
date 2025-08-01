import os,time
import requests
from log import logger
from flask import Flask, render_template, request, redirect, url_for, flash
from database import init_db, add_vm_record, update_vm_status, delete_vm_record, get_all_vm_records, get_vm_record

# 定义 Libvirt Server 的基础 URL
LIBVIRT_API_BASE_URL = "http://127.0.0.1:5001/api/v1" # 根据 libvirt_server.py 实际运行的地址和端口调整

app = Flask(__name__)
app.secret_key = 'your_super_secret_key' # Replace with a strong key for flash messages

# Load configuration from config.py into Flask app's config object
app.config.from_object('config')

# Initialize database and Libvirt manager
init_db()

@app.route('/')
def index():
    """Displays all virtual machine lists."""
    vms_from_db = get_all_vm_records() # 从你的数据库获取数据

    # 从 Libvirt Server 获取最新状态
    try:
        response = requests.get(f"{LIBVIRT_API_BASE_URL}/vms")
        response.raise_for_status() # 对 4xx/5xx 响应抛出异常
        libvirt_vms_data = response.json().get('vms', [])
        libvirt_vms_map = {vm['name']: vm for vm in libvirt_vms_data}
    except requests.exceptions.RequestException as e:
        flash(f"Error connecting to Libvirt backend: {e}. VM statuses might be outdated.", 'error')
        libvirt_vms_map = {} # 如果连接失败，则没有最新的 Libvirt 状态

    # 更新数据库中的状态以同步
    updated_vms_count = 0
    final_vms_for_display = []

    for vm_record in vms_from_db:
        vm_name_db = vm_record[0]
        # 尝试从 Libvirt Server 的响应中获取最新状态
        libvirt_vm_info = libvirt_vms_map.get(vm_name_db)

        if libvirt_vm_info:
            current_status_libvirt = libvirt_vm_info['status']
            # 如果数据库状态与 Libvirt 实际状态不一致，则更新
            if current_status_libvirt != vm_record[2]:
                update_vm_status(vm_name_db, current_status_libvirt)
                updated_vms_count += 1
            # 将最新的 Libvirt 状态信息用于显示
            final_vms_for_display.append(libvirt_vm_info)
        else:
            # 如果 Libvirt Server 报告该 VM 不存在，则从数据库中删除
            # 或者如果 Libvirt Server 没响应，则保留数据库现有状态
            # 这里选择删除（如果确定Libvirt是权威数据源）
            delete_vm_record(vm_name_db)
            flash(f"Virtual machine '{vm_name_db}' not found in Libvirt, removed from database.", 'warning')
            updated_vms_count += 1
            
    if updated_vms_count > 0:
        logger.info(f"Refreshed status for {updated_vms_count} VMs in the database.")

    # 重新从数据库获取最终列表，确保显示最新数据
    # 或者直接使用 final_vms_for_display (需要确保它包含所有你想显示的VMs)
    final_vms = get_all_vm_records() # 假设这里会反映上面的更新
    return render_template('index.html', vms=final_vms)


@app.route('/create_vm', methods=['GET', 'POST'])
def create_vm_page():
    if request.method == 'POST':
        vm_name = request.form['vm_name']
        vm_pwd = request.form['vm_pwd']
        memory_mb = int(request.form['memory_mb'])
        vcpu_count = int(request.form['vcpu_count'])

        payload = {
            "vm_name": vm_name,
            "vm_pwd": vm_pwd,
            "memory_mb": memory_mb,
            "vcpu_count": vcpu_count
        }

        try:
            # 调用 Libvirt Server 的创建 VM API
            response = requests.post(f"{LIBVIRT_API_BASE_URL}/vms", json=payload)
            response.raise_for_status() # 如果状态码不是 2xx，则抛出异常

            result_data = response.json()
            message = result_data.get('message', 'Unknown message')
            success = result_data.get('success', False)
            extra_data = result_data.get('data', {})

            if success:
                vnc_port = extra_data.get('vncport')
                link = extra_data.get('link')
                connid = extra_data.get('connid')
                # 添加到你自己的数据库
                add_vm_record(vm_name, os.path.basename(app.config['BASE_IMAGE_PATH']), 
                            '运行中', vnc_port, link, connid, memory_mb, vcpu_count)

                flash_message = f"Virtual machine '{vm_name}' created and started successfully! URL: {vnc_port}."
                flash(flash_message, 'success')
                return redirect(url_for('index'))
            else:
                flash(f"Failed to create virtual machine: {message}", 'error')

        except requests.exceptions.ConnectionError:
            flash("Failed to connect to Libvirt backend server. Please ensure it's running on 127.0.0.1:5001.", 'error')
        except requests.exceptions.HTTPError as e:
            error_detail = e.response.json().get('detail', str(e)) if e.response else str(e)
            flash(f"Libvirt server responded with an error: {error_detail}", 'error')
        except requests.exceptions.RequestException as e:
            flash(f"An unexpected error occurred during API call: {e}", 'error')

    return render_template('create_vm.html')

@app.route('/manage_vm/<vm_name>', methods=['POST'])
def manage_vm_action(vm_name):
    action = request.form.get('action')
    endpoint_map = {
        'start': f"{LIBVIRT_API_BASE_URL}/vms/{vm_name}/start",
        'stop': f"{LIBVIRT_API_BASE_URL}/vms/{vm_name}/stop",
        'destroy': f"{LIBVIRT_API_BASE_URL}/vms/{vm_name}/destroy",
        'delete': f"{LIBVIRT_API_BASE_URL}/vms", # DELETE 方法
    }
    
    # 动态确定 HTTP 方法
    api_url = endpoint_map.get(action)
    http_method = requests.post
    if action == 'delete':
        http_method = requests.delete
        connid = int(request.form.get('connid'))
        payload = {
            "vm_name": vm_name,
            "connid": connid
        }

    
    if not api_url:
        flash("Invalid operation.", 'error')
        return redirect(url_for('index'))

    try:
        if action == 'delete':
            response = http_method(api_url, json=payload)
        else:
            response = http_method(api_url)
        response.raise_for_status()
        
        result_data = response.json()
        message = result_data.get('message', 'Unknown message')
        success = result_data.get('success', False)

        if success:
            flash(message, 'success')
            # 根据操作更新本地数据库状态，或者在下次刷新时由 index 路由同步
            if action == 'start':
                update_vm_status(vm_name, '运行中')
            elif action == 'stop':
                update_vm_status(vm_name, '正在关机')
            elif action == 'destroy':
                update_vm_status(vm_name, '已关机')
            elif action == 'delete':
                delete_vm_record(vm_name) # 从数据库中移除
            
            # 对于 stop 操作，可能需要等待一小段时间再刷新状态
            if action == 'stop':
                time.sleep(2) 

        else:
            flash(f"Failed to perform action '{action}' for VM '{vm_name}': {message}", 'error')

    except requests.exceptions.ConnectionError:
        flash("Failed to connect to Libvirt backend server. Please ensure it's running.", 'error')
    except requests.exceptions.HTTPError as e:
        error_detail = e.response.json().get('detail', str(e)) if e.response else str(e)
        flash(f"Libvirt server responded with an error: {error_detail}", 'error')
    except requests.exceptions.RequestException as e:
        flash(f"An unexpected error occurred during API call: {e}", 'error')

    return redirect(url_for('index'))

if __name__ == '__main__':
    # The LibvirtManager's __init__ method now handles all environment checks and connection.
    # We simply check if the connection was successful after initialization.
    logger.info("Starting Flask application.")
    
    app.run(debug=False, host='0.0.0.0', port=5002)