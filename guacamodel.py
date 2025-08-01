import json
import requests
import base64
import copy
from typing import Tuple, Optional, Dict, Union
from log import logger

# Assuming 'Guacamole' class is correctly imported from 'client'
# and 'templates' provides the necessary dictionaries.
from client import Guacamole
from templates import (
    RDP_CONNECTION,
    VNC_CONNECTION,
    SSH_CONNECTION,
    USER,
    ORG_CONNECTION_GROUP,
    SYSTEM_PERMISSIONS,
    ADD_READ_PERMISSION,
)

class GuacamoleClient:
    """
    Guacamole client class to manage connections and user permissions.
    This class now supports the 'with' statement for resource management.
    """
    GUAC_URL_PATH = "/"
    GUAC_METHOD = "https"
    GUAC_VERIFY_SSL = False # Consider setting to True in production with proper certificate validation

    def __init__(self,
        guac_hostname: str,
        guac_username: str = "guacadmin",
        guac_password: str = "guacadmin",
    ):
        self.guac_hostname = guac_hostname
        self.guac_username = guac_username
        self.guac_password = guac_password
        self.guacamole: Optional[Guacamole] = None # Explicitly type as Optional
        self._is_initialized = False # Track if initialization was successful

    def __enter__(self):
        """
        Enters the runtime context related to this object.
        This method is called when the 'with' statement is entered.
        It handles the initialization (login) to the Guacamole server.
        """
        if self.guacamole is not None and self._is_initialized:
            logger.warning("Guacamole client already initialized. Re-entering context.")
            return self

        try:
            # Attempt to initialize the Guacamole API client.
            # This is where the actual connection/authentication to Guacamole happens.
            self.guacamole = Guacamole(
                hostname=self.guac_hostname,
                username=self.guac_username,
                password=self.guac_password,
                method=self.GUAC_METHOD,
                url_path=self.GUAC_URL_PATH,
                verify=self.GUAC_VERIFY_SSL,
            )
            self._is_initialized = True
            logger.info(f"Guacamole client successfully initialized for {self.guac_hostname} upon entering 'with' block.")
            return self # Return self to be bound to 'as' variable in 'with' statement
        except (requests.exceptions.RequestException, AssertionError) as e:
            logger.error(f"Failed to initialize Guacamole client upon entering 'with' block: {e}", exc_info=True)
            self.guacamole = None
            self._is_initialized = False
            # Re-raise the exception to indicate failure to the caller of the 'with' statement
            raise
        except Exception as e:
            logger.critical(f"An unexpected error occurred during Guacamole client initialization in __enter__: {e}", exc_info=True)
            self.guacamole = None
            self._is_initialized = False
            raise

    def __exit__(self, exc_type, exc_value, traceback):
        """
        Exits the runtime context related to this object.
        This method is called when the 'with' block is exited,
        regardless of whether an exception occurred.
        It sets self.guacamole to None to explicitly release the reference.
        """
        if self.guacamole and self._is_initialized:
            logger.info("Guacamole client context exiting. Releasing client reference.")
            # If your Guacamole client doesn't have an explicit logout method,
            # simply setting self.guacamole to None allows the object to be garbage collected.
            # The session will then naturally expire on the Guacamole server side.
            self.guacamole = None
            self._is_initialized = False
        else:
            logger.warning("Guacamole client not active, no reference to release on __exit__.")

        # If __exit__ returns True, it suppresses the exception.
        # We generally want exceptions to propagate, so we let it return None (default).
        return False # Returning False (or implicitly None) allows exceptions to propagate.

    @staticmethod
    def strtobase64(id: str) -> str:
        """
        Converts a string ID to a Base64 format typically used by Guacamole
        for client URLs. It appends 'cpostgresql' and removes trailing '=' if present.
        """
        suffix_bytes = b'\x00c\x00postgresql'
        combined_bytes = id.encode('utf-8') + suffix_bytes
        encoded = base64.b64encode(combined_bytes).decode('utf-8')
        return encoded.rstrip('=')

    def grant_user_permissions(self,
        username: str,
        userpwd: str,
        vnchost: str,
        vncport: int,
        maxconn: int = 3
    ) -> Tuple[bool, Union[str, Dict, None]]:
        """
        Grants a user permissions to a VNC connection in Guacamole.

        Args:
            username (str): The username for the new Guacamole user.
            userpwd (str): The password for the new Guacamole user.
            vnchost (str): The hostname or IP address of the VNC server.
            vncport (int): The port of the VNC server.
            maxconn (int): The maximum number of concurrent connections allowed for this Guacamole connection.

        Returns:
            Tuple[bool, Optional[str], str]:
                - bool: True if permissions were granted successfully, False otherwise.
                - Optional[str]: The Guacamole client URL if successful, None if failed.
                - str: A status message ("Success" or an error description).
        """
        # Ensure the Guacamole client was successfully initialized by __enter__.
        if not self.guacamole or not self._is_initialized:
            error_message = "Guacamole client was not successfully initialized or is not active. Please use 'with' statement correctly."
            logger.error(error_message)
            return False, error_message

        connection_id: Optional[str] = None # Explicitly type as Optional

        try:
            # 1. Add user
            logger.info(f"Attempting to add user '{username}' to Guacamole.")
            user_payload = copy.deepcopy(USER)
            user_payload["username"] = username
            user_payload["password"] = userpwd
            self.guacamole.add_user(user_payload)
            logger.info(f"User '{username}' successfully added or already exists.")

            # 2. Add connection
            conn_name = f"vm_{username}"
            logger.info(f"Attempting to add VNC connection '{conn_name}' for {vnchost}:{vncport}.")
            conn_payload = copy.deepcopy(VNC_CONNECTION)
            conn_payload["name"] = conn_name
            conn_payload["parameters"]["hostname"] = vnchost
            conn_payload["parameters"]["port"] = str(vncport)
            conn_payload["attributes"]["max-connections"] = str(maxconn)

            new_conn = self.guacamole.add_connection(conn_payload)

            if new_conn and "identifier" in new_conn:
                connection_id = new_conn["identifier"]
                logger.info(f"Connection '{conn_name}' added with ID: {connection_id}.")
            else:
                error_message = f"Failed to get connection identifier for '{conn_name}'. API response: {new_conn}"
                logger.error(error_message)
                return False, error_message

            # 3. Grant user read access to the connection
            permission_payload = [{"op": "add", "path": f"/connectionPermissions/{connection_id}", "value": "READ"}]
            logger.info(f"Granting READ permission to user '{username}' for connection {connection_id}.")

            response = self.guacamole.grant_permission(username, permission_payload)

            if response.status_code == 204:
                logger.info(f"Permission successfully granted to user '{username}'.")
            else:
                error_message = f"Permission grant for user '{username}' failed with unexpected status code: {response.status_code}. Response: {response.text}"
                logger.error(error_message)
                return False, error_message

            # 4. Construct the Guacamole client URL
            guac_base_url = f"{self.GUAC_METHOD}://{self.guac_hostname}"
            guac_client_url = f"{guac_base_url}{self.GUAC_URL_PATH}#/client/{self.strtobase64(connection_id)}"
            logger.info(f"Generated Guacamole client URL: {guac_client_url}")
            return True, {"link": guac_client_url, "connid": connection_id, "vncport": vncport}

        except requests.exceptions.HTTPError as e:
            error_message = self._parse_context(e.response.text)
            logger.error(f"HTTP error during permission grant for '{username}': {error_message}", exc_info=True)
            return False, error_message
        except requests.exceptions.ConnectionError as e:
            error_message = f"Failed to connect to Guacamole server: {e}"
            logger.error(f"Connection error during permission grant for '{username}': {error_message}", exc_info=True)
            return False, error_message
        except requests.exceptions.Timeout as e:
            error_message = f"Guacamole API request timed out: {e}"
            logger.error(f"Timeout error during permission grant for '{username}': {error_message}", exc_info=True)
            return False, error_message
        except requests.exceptions.RequestException as e:
            error_message = f"General Guacamole API request error: {e}"
            logger.error(f"General request error during permission grant for '{username}': {error_message}", exc_info=True)
            return False, error_message
        except json.JSONDecodeError as e:
            error_message = f"Failed to parse Guacamole API response JSON: {e}"
            logger.error(f"JSON decode error during permission grant for '{username}': {error_message}", exc_info=True)
            return False, error_message
        except KeyError as e:
            error_message = f"Guacamole API response is missing expected data key: {e}"
            logger.error(f"Data parsing error during permission grant for '{username}': {error_message}", exc_info=True)
            return False, error_message
        except Exception as e:
            error_message = f"An unexpected error occurred during permission grant for '{username}': {e}"
            logger.critical(f"Unhandled error: {error_message}", exc_info=True)
            return False, error_message
        
    def delete_user_and_vm(self, name, connid) -> Tuple[bool, str]:
        """
        delete user and vm link
        """
        if not self.guacamole or not self._is_initialized:
            error_message = "Guacamole client was not successfully initialized or is not active. Please use 'with' statement correctly."
            logger.error(error_message)
            return False, error_message
        
        try:
            self.guacamole.delete_connection(connection_id=connid)
            self.guacamole.delete_user(name)
            return True, "Delete Successfully"
        except Exception as e:
            error_message = f"Deleting user and link failed for '{name}': {e}"
            logger.critical(f"Unhandled error: {error_message}", exc_info=True)
            return False, error_message
    
    def _parse_context(self, rawdata: bytes) -> Optional[str]:
        """
        parse json byte
        """
        json_str = rawdata.decode('utf-8')
        data = json.loads(json_str)

        # user already exists
        if data["type"] == "BAD_REQUEST" and "already exists" in data['translatableMessage']['variables']['MESSAGE']:
            return data['translatableMessage']['variables']['MESSAGE']
        return None
