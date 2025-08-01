import sqlite3
from config import DB_PATH

def init_db():
    """初始化数据库，创建 VMs 表"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS vms (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            connid INTEGER,
            name TEXT NOT NULL UNIQUE,
            template_name TEXT NOT NULL,
            status TEXT NOT NULL,
            link TEXT NOT NULL,
            creation_time DATETIME DEFAULT CURRENT_TIMESTAMP,
            vnc_port INTEGER,
            memorysize INTEGER,
            vcpucount INTEGER
        )
    ''')
    conn.commit()
    conn.close()

def add_vm_record(
        name, 
        template_name, 
        status, 
        vnc_port, 
        link, 
        connid, 
        memorysize,
        vcpucount
):
    """添加虚拟机记录"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("INSERT INTO vms (name, template_name, status, vnc_port, link, connid, memorysize, vcpucount) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                   (name, template_name, status, vnc_port, link, connid, memorysize, vcpucount))
    conn.commit()
    conn.close()

def update_vm_status(name, status):
    """更新虚拟机状态"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("UPDATE vms SET status = ? WHERE name = ?", (status, name))
    conn.commit()
    conn.close()

def delete_vm_record(name):
    """删除虚拟机记录"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("DELETE FROM vms WHERE name = ?", (name,))
    conn.commit()
    conn.close()

def get_all_vm_records():
    """获取所有虚拟机记录"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT connid, name, template_name, status, creation_time, vnc_port, link, memorysize, vcpucount FROM vms ORDER BY creation_time DESC")
    vms = cursor.fetchall()
    conn.close()
    return vms

def get_vm_record(name):
    """获取单个虚拟机记录"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT name, template_name, status, creation_time, vnc_port link, connid, memorysize, vcpucount FROM vms WHERE name = ?", (name,))
    vm = cursor.fetchone()
    conn.close()
    return vm

if __name__ == '__main__':
    # 首次运行时执行，或者手动运行一次以创建数据库
    init_db()
    print(f"数据库已初始化: {DB_PATH}")