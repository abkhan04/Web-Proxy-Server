import select
import socket
import threading
import time
import tkinter as tk

from tkinter import scrolledtext, messagebox


class WebProxyServer:
    # Constants
    BACKLOG = 10        # Maximum number of pending connections
    BUFFER = 8192       # Buffer size for data transfer
    HTTP_PORT = 80      # Default HTTP port
    HTTPS_PORT = 443    # Default HTTPS port

    def __init__(self, host='127.0.0.1', port=4000, callback=None):
        """
        Initializes the WebProxyServer with the provided host and port.
        
        Parameters:
        - host (str): The IP address the proxy server binds to. Default is 127.0.0.1
        - port (int): The port the proxy server listens on. Default is 4000.
        - callback (function): An optional callback function for logging.
        """
        self.host = host
        self.port = port
        self.cache = {}             # Cached responses
        self.blocked_urls = set()   # Blocked URLs
        self.callback = callback    # Callback function for logging
    
    def _update_cb(self, message: str):
        """
        Helper function to invoke the callback (if exists) to update the logs.

        Parameters:
        - message (str): The message to log.
        """
        if self.callback:
            self.callback(message)
        else:
            print(message)
    
    def start(self):
        """
        Starts the web proxy server, by binding the server socket, and listening for incoming client connections.
        When a new connection is accepted, a new thread is created to handle the client request.
        """
        # Setup proxy server socket
        server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server_socket.bind((self.host, self.port))
        server_socket.listen(self.BACKLOG)

        self._update_cb(f'Proxy Server Started: ({self.host}, {self.port})')
        self._update_cb(f'Backlog set to {self.BACKLOG}!')

        while True:
            # Accept connection
            client_socket, client_address = server_socket.accept()
            self._update_cb(f'Accepted Connection: {client_address}')
            # Start a new thread to handle the client request
            threading.Thread(target=self.handle_request, args=(client_socket, client_address)).start()

    def handle_request(self, client_socket: socket.socket, client_address):
        """
        Handles an incoming client request, processes it, and sends an appropriate response.
        
        Parameters:
        - client_socket (socket.socket): The socket used to communicate with the client.
        - client_address: The IP and port of the client.
        """
        # Start timer
        start_time = time.time()

        # Receive request
        request = client_socket.recv(self.BUFFER).decode()
        self._update_cb(f'Client Request:\n{request}')

        if not request:
            client_socket.close()
            return
        
        # Extract URL and Host from request
        target_url = self.get_url(request)
        target_host = self.get_host(request)

        # If the target URL is blocked, respond with a 403 Forbidden message
        if target_url in self.blocked_urls:
            # Establish Connection with HTTPS
            if 'CONNECT' in request:
                client_socket.send(b'HTTP/1.1 200 Connection Established\r\n\r\n')
            
            # Send blocked response
            response = b'HTTP/1.1 403 Forbidden\r\n'
            response += b'Content-Type: text/html\r\n\r\n'
            response += b'<html><head><title>403 Forbidden</title></head><body><h1>403 Forbidden</h1><p>This page has been blocked by the proxy server.</p></body></html>'

            client_socket.sendall(response)
        # If the target URL is in the cache, serve the cached response
        elif target_url in self.cache:
            # Conditional GET request
            conditional_request = 'GET ' + target_url + ' HTTP/1.1\r\n'
            conditional_request += 'Host: ' + target_host + '\r\n'
            conditional_request += 'If-Modified-Since: ' + self.cache[target_url][1].decode() + '\r\n\r\n'

            conditional_response = self.forward_to_server(conditional_request, True)

            # If the status code is 304 Not Modified, use the cached response, else forward the original request and cache the new response
            if self.get_status_code(conditional_response) == b'304':
                # Cached response
                response = self.cache[target_url][0]
                client_socket.sendall(response)

                # Calculate time saved by caching the response
                end_time = time.time()
                execution_time = end_time - start_time
                time_saved = self.cache[target_url][2] - execution_time
                self._update_cb(f'Saved {time_saved} by Caching!')
            else:
                # HTTP
                response = self.forward_to_server(request)
                client_socket.sendall(response.encode())

                # Calculate time spent by forwarding the response
                end_time = time.time()
                execution_time = end_time - start_time
                last_modified = self.get_last_modified(response)

                # Cache response, last modified, and execution time
                self.cache[target_url] = [response, last_modified, execution_time]
        else:
            # Forward the response
            if 'CONNECT' in request:
                # HTTPS
                self.handle_https(client_socket, request)
            else:
                # HTTP
                response = self.forward_to_server(request)
                client_socket.sendall(response)

                # Calculate time spent by forwarding the response
                end_time = time.time()
                execution_time = end_time - start_time
                last_modified = self.get_last_modified(response)

                # Cache response, last modified, and execution time
                self.cache[target_url] = [response, last_modified, execution_time]  
        
        # Close Connection
        self._update_cb(f'Closed Connection: {client_address}\n')
        client_socket.close()

    def handle_https(self, client_socket: socket.socket, request: str):
        """
        Handles an HTTPS request by establishing a connection with the target server.

        Parameters:
        - client_socket (socket.socket): The socket to communicate with the client.
        - request (str): The HTTPS request from the client.
        """
        # Extract host from request
        target_host = self.get_host(request)
    
        # Setup the target server socket
        server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server_socket.connect((target_host, self.HTTPS_PORT))
        
        client_socket.send(b'HTTP/1.1 200 Connection Established\r\n\r\n')

        # Relay data between the client and the server
        self.relay_https(client_socket, server_socket)

    def relay_https(self, client_socket: socket.socket, server_socket: socket.socket):
        """
        Relays data between the client and server for HTTPS communication.

        Parameters:
        - client_socket (socket.socket): The socket to communicate with the client.
        - server_socket (socket.socket): The socket to communicate with the target server.
        """
        while True:
            rlist, _, _ = select.select([client_socket, server_socket], [], [])
            for s in rlist:
                data = s.recv(self.BUFFER)

                if not data:
                    client_socket.close()
                    server_socket.close()
                    return

                if s is client_socket:
                    server_socket.send(data)
                else:
                    client_socket.send(data)

    def forward_to_server(self, request: str, conditional_get=False) -> bytes:
        """
        Forwards a request to the target server and returns the response.
        
        Parameters:
        - request (str): The HTTP request to be sent to the server.
        - conditional_get (bool): If true, only receive self.BUFFER bytes to avoid infinite looping
        
        Returns:
        - bytes: The server's response.
        """
        # Extract target host
        target_host = self.get_host(request)
        
        # Setup target server socket
        server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server_socket.connect((target_host, self.HTTP_PORT))
        server_socket.sendall(request.encode())

        # If conditional_get, then only receive self.BUFFER bytes
        if conditional_get:
            server_response = server_socket.recv(self.BUFFER)
        else:
            server_response = self.recv_all(server_socket)
        
        server_socket.close()
        return server_response

    def recv_all(self, sock: socket.socket) -> bytes:
        """
        Receives all data from a socket.

        Parameters:
        - sock (socket.socket): The socket to receive data from.

        Returns:
        - bytes: All data received from the socket.
        """
        data = b''
        while True:
            chunk = sock.recv(self.BUFFER)
            if not chunk:
                break
            data += chunk
        return data
    
    def get_url(self, request: str) -> str:
        """
        Extracts the target URL from the HTTP request.

        Parameters:
        - request (str): The HTTP request.

        Returns:
        - str: The extracted target URL.
        """
        lines = request.split('\r\n')
        if lines:
            return lines[0].split()[1]
        return ''
    
    def get_status_code(self, response: bytes) -> bytes:
        """
        Extracts the status code from the HTTP response.

        Parameters:
        - response (bytes): The HTTP response.

        Returns:
        - bytes: The extracted status code.
        """
        lines = response.split(b'\r\n')
        if lines:
            return lines[0].split()[1]
        return b''

    def get_host(self, request: str) -> str:
        """
        Extracts the host (and port) from the HTTP request.

        Parameters:
        - request (str): The HTTP request.

        Returns:
        - str: The target host.
        - int: The target port.
        """
        lines = request.split('\r\n')
        for line in lines:
            if line.lower().startswith('host:'):
                host_line = line.split(':', 1)[1].strip()

                if ':' in host_line:
                    host, port = host_line.split(':', 1)
                    return host.strip()
                else:
                    return host_line
        return 'localhost'

    def get_last_modified(self, request: bytes) -> bytes:
        """
        Extracts the 'Last-Modified' header from the HTTP response.

        Parameters:
        - request (bytes): The HTTP response.

        Returns:
        - bytes: The 'Last-Modified' header value or the current time.
        """
        lines = request.split(b'\r\n')
        for line in lines:
            if line.lower().startswith(b'last-modified:'):
                return line.split(b':', 1)[1].strip()
        return time.strftime(b'%a, %d %b %Y %H:%M:%S GMT', time.gmtime())


class ManagementConsole(tk.Tk):
    def __init__(self):
        """
        Initializes the management console GUI for controlling the proxy server.
        """
        super().__init__()

        self.title('Server Management Console')
        self.geometry('600x400')
        self.configure(bg='#2C3E50')
        
        # Main Frame
        self.main_frame = tk.Frame(self, bg='#2C3E50')
        self.main_frame.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)

        # Console Frame - For displaying logs
        self.console_frame = tk.Frame(self.main_frame, bg='#2C3E50')
        self.console_frame.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=5)

        self.log_text = scrolledtext.ScrolledText(self.console_frame, height=12, width=50, state=tk.DISABLED, bg='#ECF0F1', fg='#2C3E50', font=('Arial', 10))
        self.log_text.pack(fill=tk.BOTH, expand=True)

        # Management Frame - For controlling the server
        self.manage_frame = tk.Frame(self.main_frame, bg='#2C3E50')
        self.manage_frame.pack(side=tk.RIGHT, fill=tk.BOTH, expand=False, padx=5)

        self.start_button = tk.Button(self.manage_frame, text='Start Server', command=self.start_server, bg='#27AE60', fg='white', font=('Arial', 10), padx=5, pady=3)
        self.start_button.pack(pady=10)

        # Block URL Entry and List
        self.word_entry = tk.Entry(self.manage_frame, width=20, font=('Arial', 10))
        self.word_entry.pack(pady=3)
        
        self.add_button = tk.Button(self.manage_frame, text='Add Blocked URL', command=self.add_blocked_url, bg='#27AE60', fg='white', font=('Arial', 10), padx=5, pady=3)
        self.add_button.pack(pady=3)

        self.remove_button = tk.Button(self.manage_frame, text='Remove Blocked URL', command=self.remove_blocked_url, bg='#E74C3C', fg='white', font=('Arial', 10), padx=5, pady=3)
        self.remove_button.pack(pady=3)

        self.blocked_listbox = tk.Listbox(self.manage_frame, height=10, font=('Arial', 10), bg='#ECF0F1', fg='#2C3E50')
        self.blocked_listbox.pack(pady=3, fill=tk.BOTH, expand=True)

        self.proxy_server = None

    def start_server(self):
        """
        Starts the proxy server in a separate thread when the start button is clicked.
        """
        if not self.proxy_server:
            self.proxy_server = WebProxyServer(callback=self.update_log)
            self.start_button.config(state=tk.DISABLED)
            threading.Thread(target=self.proxy_server.start, daemon=True).start()

    def update_log(self, message):
        """
        Updates the console log with a new message.
        
        Parameters:
        - message (str): The log message to display.
        """
        self.log_text.config(state=tk.NORMAL)
        self.log_text.insert(tk.END, message + '\n')
        self.log_text.config(state=tk.DISABLED)
        self.log_text.yview(tk.END)

    def add_blocked_url(self):
        """
        Adds a URL to the blocked list if it is not already blocked.
        """
        url = self.word_entry.get().strip()
        if url and url not in self.proxy_server.blocked_urls:
            self.proxy_server.blocked_urls.add(url)
            self.blocked_listbox.insert(tk.END, url)
            self.update_log(f'Added blocked URL: {url}')
        else:
            messagebox.showwarning('Warning', 'URL is empty or already blocked!')
        self.word_entry.delete(0, tk.END)

    def remove_blocked_url(self):
        """
        Removes the selected URL from the blocked list.
        """
        selected_url = self.blocked_listbox.get(tk.ACTIVE)
        if selected_url:
            self.proxy_server.blocked_urls.remove(selected_url)
            self.blocked_listbox.delete(tk.ACTIVE)
            self.update_log(f'Removed blocked URL: {selected_url}')
        else:
            messagebox.showwarning('Warning', 'No URL selected for removal!')


if __name__ == '__main__':
    app = ManagementConsole()
    app.mainloop()
