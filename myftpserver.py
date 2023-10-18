import os
import re
import socket
import threading


# '0.0.0.0' to bind to all interfaces
ADDRESS = '0.0.0.0'
# default port for FTP control connection
PORT = 21

# receive / transmit buffer size
BUFFER_SIZE = 2048
# encoding to use for sending / receiving messages
ENCODING = 'utf-8'
# path in the file system where the server started
BASE_PATH = os.getcwd()
# currently supported commands
SUPPORTED_COMMANDS = ['OPTS', 'USER', 'PASS', 'QUIT',
                      'XPWD', 'CWD', 'DELE', 'PORT', 'RETR', 'STOR']

# socket for the exchange of commands and replies
control_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
# reuse a local socket in TIME_WAIT state; prevent OSError: [Errno 98] Address already in use
control_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
# bind the socket to the address and port
control_socket.bind((ADDRESS, PORT))


def send_message(cnx, message):
    ''' Sends message to the client

    Arguments:
        cnx: control socket connection with the client
        message: message to send to the client over the connection
    '''

    cnx.send(f'{message}\r\n'.encode(ENCODING))


def close_cnx(cnx):
    ''' Closes the connection with the client

    Arguments:
        cnx: control socket connection with the client
    '''

    cnx.close()
    print('[SERVER] Client disconnected.')


def connect_to_client(client_info):
    ''' Establishes a data connection with the client

    Arguments:
        client_info: address sent by the client (h1,h2,h3,h4,p1,p2)

    Returns:
        socket | None : data communication socket if connected to the client else None
    '''

    # client_address = h1.h2.h3.h4
    client_address = '.'.join(client_info[:4])
    # client_port = (p1 * 256) + p2
    client_port = int(client_info[4]) * 256 + int(client_info[5])

    # socket for the exchange of data
    data_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)

    try:
        # connect to the client
        data_socket.connect((client_address, client_port))
        print('[SERVER] Data connection established.')
    except ConnectionRefusedError:
        data_socket = None

    return data_socket


def handle_unauthenticated(cnx):
    ''' Sends status 530, closes the control connection and sets appropriate flags if not authenticated

    Arguments:
        cnx: control socket connection with the client
    '''

    send_message(cnx, '530 Not logged in.')
    close_cnx(cnx)

    connected = False

    return connected


def handle_connected_client(cnx, addr):
    ''' Handles the session between the server and the client

    Arguments:
        cnx: control socket connection with the client
        addr: address of the connected client
    '''

    # Welcome the connected client
    send_message(cnx, '220 Welcome to myFTPserver!')

    username = None
    password = None

    # client address for data communication
    client_info = None
    # socket for full duplex connection over which data is transferred
    data_socket = None

    # server root directory
    current_path = '/'

    # flag to check if a client is authenticated
    authenticated = False
    # flag to check if a client is connected
    connected = True

    while connected:
        # when connected wait for message from the client
        try:
            message = cnx.recv(BUFFER_SIZE).decode(ENCODING).strip()
        except ConnectionResetError:
            close_cnx(cnx)
            connected = False
            continue

        if not message:
            close_cnx(cnx)
            connected = False
            continue

        # print the whole received command
        print(f'[SERVER] Received command : {message}')

        # split the message into command and arguments
        split_message = re.findall('^[A-Z]{3,4}(?=\s?)|(?<=\s).+', message)
        command = split_message[0]

        # prevent exception when message has no arguments
        # e.g. PWD command has no arguments
        try:
            arg = split_message[1].strip()
        except IndexError:
            arg = ''

        # continue immediately if command is unsupported
        if command not in SUPPORTED_COMMANDS:
            send_message(cnx, '502 Command not implemented.')
            continue

        # handle OPTS command
        if command == 'OPTS':
            if not arg.startswith('UTF8'):
                # send status 501 if argument does not start with UTF8
                # other arguments not yet supported
                send_message(
                    cnx, '504 Command not implemented for that argument.')

            # send status 200 if argument starts with UTF8
            send_message(cnx, '200 OK')

            continue

        # handle USER command
        elif command == 'USER':

            username = arg
            # send status 331 and ask for password
            send_message(cnx, '331 Username OK, need password.')

            continue

        # handle PASS command
        elif command == 'PASS':

            password = arg

            if username == 'guest' and password == 'guest':
                # send status 230 for successful login
                send_message(cnx, '230 Login successful.')
                print('[SERVER] USER authenticated.')

                # set the authenticated flag
                authenticated = True

            else:
                # clear username and password
                username, password = None, None

                # set appropriate flags
                connected, authenticated = False, False

                # send status 530 for failed authentication
                send_message(cnx, '530 Not logged in. Incorrect credentials.')
                # close the control conection
                close_cnx(cnx)

            continue

        # handle QUIT command
        elif command == 'QUIT':
            # send status 221
            send_message(cnx, '221 Goodbye.')
            # close the control connection
            close_cnx(cnx)

            connected = False
            authenticated = False
            continue

        # commands below this requires authenticated user
        if not authenticated:
            connected = handle_unauthenticated(cnx)
            continue

        # handle XPWD command
        if command == 'XPWD':
            send_message(
                cnx, f'257 Current directory: {current_path}')
            continue

        # handle CWD command
        elif command == 'CWD':
            # create a fully qualified absolute path
            path = os.path.join(BASE_PATH, arg[1:]) if arg[:1] in [
                '/', '\\'] else os.path.join(BASE_PATH, current_path[1:], arg)

            try:
                # try changing to the absolute path
                os.chdir(path)
            except (FileNotFoundError, NotADirectoryError):
                # send status 550 if directory could not be changed
                send_message(cnx, f'550 {arg} is not a directory.')
            else:
                # get the current directory
                navigating_path = os.getcwd()

                # prevent navigating up from the root directory
                if BASE_PATH not in navigating_path:
                    # change to BASE_PATH if user navigated below the server root
                    os.chdir(BASE_PATH)

                    navigating_path = BASE_PATH
                    # reset current_path to server root
                    current_path = '/'

                current_path = '/' if navigating_path == BASE_PATH else navigating_path.removeprefix(
                    BASE_PATH)

                send_message(
                    cnx, f'250 Changed directory: {current_path}')

            continue

        # handle DELE command
        elif command == 'DELE':

            # create a fully qualified absolute path
            file_path = os.path.join(BASE_PATH, arg[1:]) if arg[:1] in [
                '/', '\\'] else os.path.join(BASE_PATH, current_path[1:], arg)

            # if arg is not a file
            if not os.path.isfile(file_path):
                # reset file_path
                file_path = ''
                # send status 550 and continue
                send_message(
                    cnx, f'550 {arg} does not exist in {current_path} directory.')
                continue

            # delete the requested file
            os.remove(file_path)
            file_path = ''

            # send status 250 for successful deletion
            send_message(cnx, f'250 {arg} deleted.')
            continue

        # handle PORT command
        elif command == 'PORT':
            # store the client address
            client_info = arg.split(',')

            send_message(cnx, f'200 Command successful.')
            continue

        # handle RETR command
        elif command == 'RETR':
            # create a fully qualified absolute path
            file_path = os.path.join(BASE_PATH, arg[1:]) if arg[:1] in [
                '/', '\\'] else os.path.join(BASE_PATH, current_path[1:], arg)

            # if requested file not found, send status 550 and continue
            if not os.path.isfile(file_path):
                file_path = ''
                send_message(
                    cnx, f'550 {arg} does not exist in {current_path} directory.')
                continue

            # establish a data connection with the client
            data_socket = connect_to_client(client_info)

            # send status 425 on connection failure
            if not data_socket:
                send_message(cnx, '425 Can\'t open data connection.')
                continue

            send_message(
                cnx, '150 File status okay; about to begin transfer.')

            # open and send the requested file
            with open(file_path, 'rb') as file:
                data_socket.sendfile(file)
                # close the data connection socket
                data_socket.close()

            print(f'[SERVER] Successfully transferred {arg}.')

            client_info = None
            data_socket = None
            file_path = ''

            send_message(cnx, f'250 Successfully transferred {arg}.')
            continue

        # handle STOR command
        elif command == 'STOR':
            # create a fully qualified absolute path
            file_path = os.path.join(BASE_PATH, arg[1:]) if arg[:1] in [
                '/', '\\'] else os.path.join(BASE_PATH, current_path[1:], arg)

            # if file already exists, send status 553
            if os.path.isfile(file_path):
                send_message(
                    cnx, f'553 Requested action not taken. {arg} name already taken.')
                file_path = ''
                continue

            # establish a data connection with the client
            data_socket = connect_to_client(client_info)

            # send status 425 on connection failure
            if not data_socket:
                send_message(cnx, '425 Can\'t open data connection.')
                continue

            send_message(
                cnx, '150 File status okay; about to begin transfer.')

            # save the incoming file
            with open(file_path, 'wb') as file:
                while True:
                    # receive and write bytes to file
                    data = data_socket.recv(BUFFER_SIZE)
                    file.write(data)

                    if not data:
                        # close the data connection if all bytes received
                        data_socket.close()
                        print(f'[SERVER] Received {arg}')
                        break

            print(f'[SERVER] Successfully saved {arg} in {current_path}.')

            file_path = ''

            send_message(cnx, f'250 Successfully transferred {arg}.')
            continue


def start_ftp_server():
    print('[SERVER] Starting...')

    # start listening for connections
    control_socket.listen()
    print(f'[SERVER] Listening on address {ADDRESS}:{PORT}')

    while True:
        # accept any incoming connections
        cnx, addr = control_socket.accept()

        # handle the connected client in a new thread
        thread = threading.Thread(
            target=handle_connected_client, args=(cnx, addr))
        thread.start()

        print(f"[SERVER] [NEW CONNECTION] : {addr} connected.")
        print(
            f'[SERVER] [ACTIVE CONNECTIONS] : {threading.active_count() - 1}')


# only allow execution as the main program
if __name__ == '__main__':

    os.system('cls')

    start_ftp_server()
