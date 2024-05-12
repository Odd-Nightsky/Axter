#!/usr/bin/env python3
import json
import logging
import signal

from time import sleep, strftime
from typing import Optional
from urllib.request import urlopen, Request, urlretrieve
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from http.client import RemoteDisconnected
from os.path import abspath

from requests import post
from PyQt6.QtCore import QVariant, QMetaType
from PyQt6.QtDBus import QDBusInterface, QDBus
CallMode = QDBus.CallMode

# for later use :3
uint = QMetaType.Type.UInt.value
QVariantMap = QMetaType.Type.QVariantMap.value
interface = QDBusInterface('org.kde.plasmashell', '/PlasmaShell', 'org.kde.PlasmaShell')

# logging
# TODO: read log level from file for easier changing
logger = logging.getLogger(__name__)

# log to file
logger.addHandler(logging.FileHandler(strftime('logs/%Y-%m-%d.log'), 'a'))
# log to STDERR (default behaviour for an empty stream handler)
logger.addHandler(logging.StreamHandler())
logger.setLevel(logging.DEBUG)


def set_desktop(file_path: str, monitor: str) -> None:
    """
    sets a desktop background through QDBus.
    :param file_path: path to the image to be set as a desktop background.
    :param monitor: string (containing an int) of the monitor it is to be set to.
    """
    monitor = QVariant(monitor)
    monitor.convert(QMetaType(uint))
    file_path = abspath(file_path)
    conf = QVariant({'Image': file_path})
    conf.convert(QMetaType(QVariantMap))
    interface.call(CallMode.Block, 'setWallpaper', 'org.kde.image', conf, monitor)


class Axter:
    def __init__(self, api_token: str, state: dict) -> None:
        """
        initialise the bot.
        :param api_token: telegram API token.
        :param state: the bots state as a dict.
        """
        self.state = state
        self.urlbase = 'https://api.telegram.org/bot'
        self.token = api_token
        self.offset = self.state['offset']
        self.accepted_types = ['image/png', 'image/jpeg', 'image/jxl']
        self.shutdown_primed = False  # we use this later to see if we're about to shut down

        # register sigterm handler so we can cleanly shut down
        signal.signal(signal.SIGTERM, self.handle_sigterm)

    def handle_sigterm(self, _signum: object, _stack: object) -> None:
        """
        sigterm handler so we can make sure we shut down cleanly.
        :param _signum: idk but if it's not here it crash.
        :param _stack: same as above.
        """
        logger.info('SIGTERM caught')
        self.shutdown_primed = True

    def request(self, function: str, method: Optional[str] = 'get', **kwargs: object) -> dict:
        """
        sends an HTTP request for a Telegram API method.
        :param function: Telegram API method to call.
        :param method: HTTP method (only post and get supported atm).
        :param kwargs: other arguments to be added to the request.
         will automatically be turned into URL args or extra data.
        :return: a dict containing the JSON response given from Telegram.
        """
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
                logger.debug(f'Sending request to: {url}\nwith data: {data}\nand headers: {headers}'
                             .replace(self.token, '<TOKEN>'))
                _request = Request(url, data, headers)
                response = urlopen(_request)
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

    def send_message(self, destination: str, text: str) -> None:
        """
        shorthand for ``self.request('sendMessage',chat_id=<destination>,text=<text>)``.
        :param destination: chat ID to send the message to.
        :param text: message text.
        """
        self.request('sendMessage', chat_id=destination, text=text)

    def send_photo(self, destination: str, filepath: str, text: str) -> None:
        """
        sends a photo.
        :param destination: chat ID to send the message to.
        :param filepath: file path for the image to be sent.
        :param text: text to accompany the image.
        """
        # this will cause issues in the future but let's pray for now
        url = f'{self.urlbase}{self.token}/sendPhoto'
        post(url, data={'chat_id': destination, 'caption': text}, files={'photo': open(filepath, 'rb')})

    def handle_updates(self) -> None:
        """
        handles getting updates from the API & responding to them.
        """
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

    def handle_message(self, message: dict) -> None:
        """
        handles incoming messages.
        :param message: the message being handled.
        """
        # set variables
        message = message['message']
        sender = str(message['from']['id'])

        # load user state
        if sender in self.state['users']:
            logger.info('User is known')
        else:
            if 'text' not in message.keys():
                return
            logger.info('New user')
            if sender == self.state['owner']:
                logger.info('Owner\'s first message. setting allowed')
                self.state['users'][sender] = {
                    "username": message['from']['username'],
                    "file_id": '',
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
                    "file_id": '',
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

        if 'text' in message:
            self.handle_commands(message, sender)
        elif 'photo' in message or 'document' in message:
            self.handle_image(message, sender)

    # TODO: add video file support
    # TODO: add crop mode options
    def handle_commands(self, message: dict, sender: str) -> None:
        """
        handles commands.
        :param message: message being handled.
        :param sender: the ID of the person who sent the message.
        """
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

    def handle_image(self, message: dict, sender: str) -> None:
        """
        handles images being sent to the bot.
        :param message: message being handled.
        :param sender: the ID of the person who sent the message.
        """
        # logger.debug(message)
        if 'photo' in message:
            # if len(message['photo']) > 2:
            #    self.send_message(sender, 'only a single image can be used. assuming first.')

            # telegram gives a list of possible sizes of the photo
            # we *SHOULD* see which one is the biggest... but the telegram API seems to put that last, and I'm lazy
            file = message['photo'][-1]
        elif 'document' in message:
            if not message['document']['mime_type'] in self.accepted_types:
                self.send_message(
                    sender,
                    f'mimetype {message["document"]["mime_type"]} unsupported.\nCurrently supported:' +
                    ', '.join(self.accepted_types))
                return
            file = message['document']
        else:
            logger.warning('this code should not run')
            self.send_message(
                sender,
                f'you somehow did something that should not happen. tell @{self.state['owner']}'
            )
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
        self.state['users'][sender]['file_id'] = file['file_id']

    def handle_callback(self, callback: dict) -> None:
        """
        handles callbacks from the inline keyboard
        only used for new users & when a monitor has been selected
        :param callback: the callback being handled
        """
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
            self.send_photo(
                self.state['owner'],
                f'images/{file_id}',
                f'New desktop set by @{self.state["users"][user_id]["username"]}'
            )
            self.send_message(user_id, 'Desktop set!')

    def save(self) -> None:
        """
        saves the bot's state to ``state.json``.
        """
        logger.info('Saving state...')
        self.state['offset'] = self.offset
        json.dump(self.state, open('state.json', 'w'), indent=2)
        logger.info('State saved.')

    def shutdown(self) -> None:
        """
        shuts down the bot.
        """
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
        logger.warning('KeyboardInterrupt. priming shutdown')
        bot.shutdown_primed = True
