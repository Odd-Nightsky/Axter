#!/usr/bin/env python3
import json
import logging

from time import sleep
from urllib.request import urlopen, Request, urlretrieve
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from http.client import RemoteDisconnected
from os.path import abspath

from PyQt6.QtCore import QVariant, QMetaType
from PyQt6.QtDBus import QDBusInterface, QDBus
CallMode = QDBus.CallMode

# for later use :3
uint = QMetaType.Type.UInt.value
QVariantMap = QMetaType.Type.QVariantMap.value
interface = QDBusInterface('org.kde.plasmashell', '/PlasmaShell', 'org.kde.PlasmaShell')

# logging
logging.basicConfig()
logger = logging.getLogger('Axter')
logger.setLevel(logging.INFO)


def set_desktop(file_path, monitor):
    monitor = QVariant(monitor)
    monitor.convert(QMetaType(uint))
    file_path = abspath(file_path)
    conf = QVariant({'Image': file_path})
    conf.convert(QMetaType(QVariantMap))
    interface.call(CallMode.Block, 'setWallpaper', 'org.kde.image', conf, monitor)


class Axter:
    def __init__(self, api_token, state):
        self.state = state
        self.urlbase = 'https://api.telegram.org/bot'
        self.token = api_token
        self.offset = self.state['offset']
        self.accepted_types = ['image/png', 'image/jpeg', 'image/jxl']
        self.shutdown_primed = False  # we use this later to see if we're about to shut down

        # register sigterm handler so we can cleanly shut down
        signal.signal(signal.SIGTERM, self.handle_sigterm)

    def handle_sigterm(self, _signum, _stack):
        # not sure if we need either value for our use case. _ here to make IDE not yell at me
        # handle sigterm, so we can make sure we're not in the middle of something
        logger.info('SIGTERM caught')
        self.shutdown_primed = True

    def request(self, function, method='get', **kwargs):
        try:
            if method == 'get':
                if not kwargs:
                    url = f'{self.urlbase}{self.token}/{function}'
                else:
                    args = urlencode(kwargs)
                    url = f'{self.urlbase}{self.token}/{function}?{args}'
                logger.debug(f'Sending request to {url}'.replace(self.token, '<TOKEN>'))
                response = urlopen(url)
            else:
                if not kwargs:
                    raise ValueError
                url = f'{self.urlbase}{self.token}/{function}'
                headers = {}
                data = json.dumps(kwargs).encode("utf-8")
                headers["Content-Length"] = len(data)
                headers["Content-Type"] = "application/json"
                logger.debug(f'Sending request to {url}\nwith data {data}\nand headers{headers}'
                             .replace(self.token, '<TOKEN>'))
                request = Request(url, data, headers)
                response = urlopen(request)
            response_json = json.load(response)
            return response_json['result']
        except HTTPError as e:
            body = json.loads(e.read().decode())  # Read the body of the error response
            logger.error(body)
            raise e
        except URLError:
            # this could cause recursion issues if it keeps resetting, but
            # we're already crashing if one reset happens so
            # if not isinstance(e, URLError) and not e.errno == 104:
            #     raise e
            logger.warning('Connection reset happened.')
            return self.request(function, method, **kwargs)
        except RemoteDisconnected:
            logger.warning('Remote closed connection.')
            return self.request(function, method, **kwargs)

    def send_message(self, destination, text):
        self.request('sendMessage', chat_id=destination, text=text)

    def handle_updates(self):
        # shutdown if we've received a SIGTERM
        if self.shutdown_primed:
            self.shutdown()
        messages = self.request('getUpdates', offset=self.offset)
        for message in messages:
            # update offset
            if message['update_id'] >= self.offset:
                self.offset = message['update_id']+1
                logger.debug(f'set offset to {self.offset}')

            if 'message' in message:
                self.handle_message(message)
            elif 'callback_query' in message:
                self.handle_callback(message)
            else:
                logger.debug(f'no handler for message type {message}')

    def handle_message(self, message):
        # set variables
        message = message['message']
        sender = str(message['from']['id'])

        # load user state
        if sender in self.state['users']:
            logger.info('User is known')
            state = self.state['users'][sender]
        else:
            logger.info('New user')
            if sender == self.state['owner']:
                logger.info('Owner\'s first message. setting allowed')
                self.state['users'][sender] = {
                    "username": message['from']['username'],
                    "state": "",
                    "stage": 0,
                    "allowed": True
                }
                self.send_message(sender, 'Hello owner!\nAdded to allowed users.')
                self.request(
                    'setMyCommands',
                    method='post',
                    commands=[
                        {
                            'command': '/ping',
                            'description': 'pong'
                        },
                        {
                            'command': '/shutdown',
                            'description': 'shuts down the bot'
                        },
                        {
                            'command': '/reset',
                            'description': 'resets users'
                        },
                        {
                            'command': '/desktop',
                            'description': 'sets desktop background'
                        }
                    ],
                    scope={
                        'type': 'chat',
                        'chat_id': self.state['owner']
                    }
                )
                return
            if message['text'] == '/start':
                if 'username' not in message['from']:
                    logger.info('New user without a username')
                    self.send_message(sender, 'Users without a username are unsupported.')
                    return
                logger.info('User is unknown, adding to state...')
                self.state['users'][sender] = {
                    "username": message['from']['username'],
                    "state": "",
                    "stage": 0,
                    "allowed": False
                }
                self.send_message(sender, 'Hello new user!\nPlease wait until my owner confirms you.')
                # fill these with None if they don't exist or the bot fucking crashes
                if 'first_name' not in message['from']:
                    message['from']['first_name'] = None
                if 'last_name' not in message['from']:
                    message['from']['last_name'] = None
                self.request(
                    'sendMessage',
                    method='post',
                    chat_id=self.state['owner'],
                    text=f'New user needing confirmation\nID: {sender}\n' +
                    f'First name: {message["from"]["first_name"]}\n' +
                    f'Last name: {message["from"]["last_name"]}\n' +
                    f'Username: @{message["from"]["username"]}',
                    reply_markup={
                        'inline_keyboard': [
                            [
                                {
                                    'text': 'allow',
                                    'callback_data': f'new_user:allow:{sender}'
                                },
                                {
                                    'text': 'ban',
                                    'callback_data': f'new_user:ban:{sender}'
                                }
                            ]
                        ]
                    }
                )
            return

        # banned users
        if not self.state['users'][sender]['allowed']:
            logger.info('user is banned')
            return

        match state['state']:
            case '':
                if 'text' in message:
                    self.handle_commands(message, sender, state)
            case '/desktop':
                self.handle_desktop(message, sender, state)

    def handle_commands(self, message, sender, state):
        # handle commands
        logger.info(f'message contents: "{message["text"]}"')
        match message['text']:
            case '/start':
                self.send_message(sender, 'You\'re already confirmed :3')

            case '/ping':
                self.send_message(sender, 'Pong!')

            case '/shutdown':
                if not sender == self.state['owner']:
                    return
                self.send_message(sender, 'Shutting down...')
                self.shutdown()

            case '/desktop':
                state['state'] = '/desktop'
                state['stage'] = 0
                self.send_message(
                    sender,
                    'Okay!\nPlease send me the image file to set as desktop.\nalternatively /cancel to stop.'
                )

            case '/reset':
                if not sender == self.state['owner']:
                    return
                logger.info('Resetting state...')
                self.state['users'] = {}
                self.send_message(sender, 'State reset')
                logger.info('State reset.')

            case '/save':
                self.save()
                self.send_message(sender, 'State saved')

    def handle_desktop(self, message, sender, state):
        if 'text' in message:
            if message['text'] == '/cancel':
                self.send_message(sender, 'Cancelling...')
                state['state'] = ''
                return

        if 'photo' in message:
            self.send_message(sender, 'Please send the image uncompressed.')

        if 'document' in message:
            if not message['document']['mime_type'] in self.accepted_types:
                self.send_message(
                    sender,
                    f'mimetype {message["document"]["mime_type"]} unsupported.\nCurrently supported:' +
                    ', '.join(self.accepted_types))
                return

            self.request(
                'sendMessage',
                method='post',
                chat_id=sender,
                text='Great!\nNow, which monitor should it apply to?',
                reply_markup={
                    'inline_keyboard': [
                        [
                            {
                                'text': 'Left',
                                'callback_data': f'desktop:1:{sender}'
                            },
                            {
                                'text': 'Primary',
                                'callback_data': f'desktop:0:{sender}'
                            },
                            {
                                'text': 'Right',
                                'callback_data': f'desktop:2:{sender}'
                            }
                        ]
                    ]
                }
            )
            self.state['users'][sender]['file_id'] = message['document']['file_id']

    def handle_callback(self, callback):
        # for now the only thing calling this should be the handling to ban/allow specific users
        callback = callback['callback_query']
        data = callback['data']
        if data.startswith('new_user'):
            _, action, user_id = data.split(':')
            if action == 'allow':
                self.state['users'][user_id]['allowed'] = True
            elif action == 'ban':
                return  # this is actually the default
            else:
                return
            self.send_message(self.state['owner'], f'Confirmed @{self.state["users"][user_id]["username"]}!')
            self.send_message(user_id, 'You\'ve been confirmed.')
            self.request(
                'editMessageText',
                method='post',
                chat_id=callback['message']['chat']['id'],
                message_id=callback['message']['message_id'],
                text=callback['message']['text'],
                reply_markup=""
            )
            self.request(
                'setMyCommands',
                method='post',
                commands=[
                    {
                        'command': '/ping',
                        'description': 'pong'
                    },
                    {
                        'command': '/desktop',
                        'description': 'sets desktop background'
                    }
                ],
                scope={
                    'type': 'chat',
                    'chat_id': user_id
                }
            )
        elif data.startswith('desktop'):
            _, monitor, user_id = data.split(':')
            file_id = self.state['users'][user_id]['file_id']
            file_info = self.request('getFile', file_id=file_id)
            urlretrieve(f'https://api.telegram.org/file/bot{self.token}/{file_info["file_path"]}', f'images/{file_id}')
            set_desktop(f'images/{file_id}', monitor)
            self.state['users'][user_id]['state'] = ''
            self.request(
                'editMessageText',
                method='post',
                chat_id=callback['message']['chat']['id'],
                message_id=callback['message']['message_id'],
                text=callback['message']['text'],
                reply_markup=""
            )
            self.request(
                'sendDocument',
                chat_id=self.state['owner'],
                document=file_id,
                caption=f'New desktop set by @{self.state["users"][user_id]["username"]}')
            self.send_message(user_id, 'Desktop set!')

    def save(self):
        logger.info('Saving state...')
        self.state['offset'] = self.offset
        json.dump(self.state, open('state.json', 'w'), indent=2)
        logger.info('State saved.')

    def shutdown(self):
        logger.info('Shutdown called')
        self.save()
        exit()


if __name__ == '__main__':
    token = open('Token', 'r').read()
    with open('state.json') as state_file:
        bot_state = json.load(state_file)
    bot = Axter(token, bot_state)
    # enter endless loop
    try:
        logger.info('Starting bot')
        while True:
            bot.handle_updates()
            sleep(1)
    except KeyboardInterrupt:
        logger.warning('KeyboardInterrupt. shutting down')
        bot.shutdown()
