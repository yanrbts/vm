import requests
import json
import logging

# 配置日志，便于查看请求和响应
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# --- Guacamole API 配置 ---
# 请根据您的实际 Guacamole 部署修改这些值
# 确保 GUACAMOLE_BASE_URL 是 Guacamole Web UI 的根URL，例如 "http://192.168.3.132:8443/guacamole/"
# API 路径通常是在此基础上加上 "api/tokens"
GUACAMOLE_WEB_ROOT_URL = "http://192.168.3.132:8443/" 
GUACAMOLE_API_TOKENS_ENDPOINT = f"{GUACAMOLE_WEB_ROOT_URL}api/tokens"

# 认证凭据
USERNAME = "guacadmin"
PASSWORD = "guacadmin" # 强烈建议在实际环境中修改默认密码

def get_guacamole_token(username, password):
    """
    通过 Guacamole REST API 获取认证令牌。
    """
    logger.info(f"尝试从 {GUACAMOLE_API_TOKENS_ENDPOINT} 获取令牌...")

    # 请求体必须是 x-www-form-urlencoded 格式
    payload = {
        "username": username,
        "password": password
    }

    # Headers for x-www-form-urlencoded
    headers = {
        "Content-Type": "application/x-www-form-urlencoded"
    }

    try:
        response = requests.post(GUACAMOLE_API_TOKENS_ENDPOINT, data=payload, headers=headers)
        
        # 打印原始响应状态码和内容，便于调试
        logger.info(f"HTTP 状态码: {response.status_code}")
        logger.info(f"响应内容: {response.text}")

        response.raise_for_status() # 如果状态码不是 2xx，则抛出 HTTPError 异常

        token_data = response.json()
        auth_token = token_data.get("authToken")

        if auth_token:
            logger.info(f"成功获取 Guacamole 认证令牌: {auth_token[:10]}...") # 只显示前10个字符
            return auth_token
        else:
            logger.error("响应中未找到 'authToken' 字段。")
            return None

    except requests.exceptions.HTTPError as http_err:
        logger.error(f"HTTP 错误发生: {http_err}")
        logger.error(f"响应体: {response.text}")
        return None
    except requests.exceptions.ConnectionError as conn_err:
        logger.error(f"连接错误发生: {conn_err}。请检查 Guacamole 服务是否运行，网络是否可达。")
        return None
    except requests.exceptions.Timeout as timeout_err:
        logger.error(f"请求超时: {timeout_err}")
        return None
    except requests.exceptions.RequestException as req_err:
        logger.error(f"请求发生未知错误: {req_err}")
        return None
    except json.JSONDecodeError as json_err:
        logger.error(f"无法解析 JSON 响应: {json_err}. 响应内容: {response.text}")
        return None

if __name__ == "__main__":
    token = get_guacamole_token(USERNAME, PASSWORD)
    if token:
        print("\n令牌获取成功，您可以使用此令牌进行后续 API 调用或构建直接访问 URL。")
    else:
        print("\n令牌获取失败，请检查日志中的错误信息。")

