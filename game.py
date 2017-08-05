#!/usr/bin/env python3
"""
This game allows the swadges to vote and control a game.
"""

from autobahn.asyncio.wamp import ApplicationSession, ApplicationRunner
from autobahn.wamp import auth
from xdo import Xdo
import asyncio
import time

keycodes = {
  "up"    : "Up",
  "down"  : "Down",
  "left"  : "Left",
  "right" : "Right",
  "select": "BackSpace",
  "start" : "Return",
  "a"     : "x",
  "b"     : "z",
}

class Button:
    """ Button name constants"""

    UP = "up"
    DOWN = "down"
    LEFT = "left"
    RIGHT = "right"
    SELECT = "select"
    START = "start"
    A = "a"
    B = "b"


class Color:
    """Some common colors"""
    RED = 0x020000
    ORANGE = 0xff7f00
    YELLOW = 0xffff00
    GREEN = 0x000200
    CYAN = 0x00ffff
    BLUE = 0x0000ff
    PURPLE = 0x7f00ff
    PINK = 0xff00ff

    WHITE = 0x010101
    BLACK = 0x000000
    OFF = 0x000000

    RAINBOW = [RED, ORANGE, YELLOW, GREEN, CYAN, BLUE, PURPLE]


# WAMP Realm; doesn't change
WAMP_REALM = "swadges"
WAMP_URL = "ws://api.swadge.com:1337/ws"

# WAMP Credentials; you will get your own later
WAMP_USER = "demo"
WAMP_PASSWORD = "hunter2"

# This is a unique name for this game
# Change this before you run it, otherwise it will conflict!
GAME_ID = "magfest_plays"

# Buttons are [u]p, [l]eft, [d]own, [r]ight, s[e]lect, [s]tart, [a], [b]
GAME_JOIN_SEQUENCE = "abab"
GAME_JOIN_LOCATION = ""


class PlayerInfo:
    # How long, in milliseconds, the player needs to hold the button to quit the game
    QUIT_TIME = 1500

    def __init__(self, badge_id, subscriptions=None):
        self.badge_id = badge_id
        self.current_button = None

        # The last timestamp of the start button press
        # Used to check if the start button is ever held for more than QUIT_TIME seconds
        self.start_press_at = 0

        # The index of the currently selected light
        self.selected_light = 0

        # The brightness level of each light. The lights are suuuper bright, and having them set
        # all the way up for long periods of time will drain the batteries and blind everyone
        self.brightness = .1

        # Keep track of what the lights are set to
        self.light_settings = [Color.WHITE, Color.WHITE, Color.WHITE, Color.WHITE]

        # Subscriptions that have been made for the player
        # Needed so we can unsubscribe later
        self.subscriptions = subscriptions or []

    def on_start_press(self, timestamp):
        self.start_press_at = timestamp

    def start_held(self, timestamp):
        """
        Returns whether or not the given timestamp is more than QUIT_TIME after the last time the
        start button was pressed.
        :param timestamp: The timestamp to check
        :return: True if timestamp is more than QUIT_TIME after the last start press
        """

        return timestamp - self.start_press_at > PlayerInfo.QUIT_TIME


class GameComponent(ApplicationSession):
    players = {}
    current_button = None
    last_button = None
    press_counter = 0
    save_counter = 0
    xdo = Xdo()
    window = xdo.search_windows(winclass="mGBA".encode('ASCII'))[0]

    def onConnect(self):
        """
        Called by WAMP upon successfully connecting to the crossbar server
        :return: None
        """
        self.join(WAMP_REALM, ["wampcra"], WAMP_USER)

    def onChallenge(self, challenge):
        """
        Called by WAMP for authentication.
        :param challenge: The server's authentication challenge
        :return:          The client's authentication response
        """
        if challenge.method == "wampcra":
            signature = auth.compute_wcs(WAMP_PASSWORD.encode('utf8'),
                                         challenge.extra['challenge'].encode('utf8'))
            return signature.decode('ascii')
        else:
            raise Exception("don't know how to handle authmethod {}".format(challenge.method))

    async def game_register(self):
        """
        Register the game with the server. Should be called after initial connection and any time
        the server requests it.
        :return: None
        """

        res = await self.call('game.register',
                              GAME_ID,
                              sequence=GAME_JOIN_SEQUENCE,
                              location=GAME_JOIN_LOCATION)

        err = res.kwresults.get("error", None)
        if err:
            print("Could not register:", err)
        else:
            # This call returns any players that may have already joined the game to ease restarts
            players = res.kwresults.get("players", [])
            await asyncio.gather(*(self.on_player_join(player) for player in players))

    async def on_button_release(self, button, timestamp=0, badge_id=None):
        """
        Called when a button is released.
        :param button:   The name of the button that was released
        :param badge_id: The ID of the badge that released the button
        :return: None
        """
        player = self.players.get(badge_id, None)

        if not player:
            print("Unknown player:", badge_id)
            return
        # Remove the player from the game if they hold down the start button for a bit
        if button == Button.START:
            if player.start_held(timestamp):
                await self.kick(badge_id)
        player.current_button = None
        self.last_button = None
        await self.calculate_buttons()

    async def set_lights(self, player):
        # Set the lights for the badge to simple colors
        # Note that the order of the lights will be [BOTTOM_LEFT, BOTTOM_RIGHT, TOP_RIGHT, TOP_LEFT]
        self.publish('badge.' + str(player.badge_id) + '.lights_static', *player.light_settings)


    async def on_button_press(self, button, timestamp=0, badge_id=None):
        """
        Called when a button is pressed.
        :param button:   The name of the button that was pressed
        :param badge_id: The ID of the badge that pressed the button
        :return: None
        """

        player = self.players.get(badge_id, None)

        if not player:
            print("Unknown player:", badge_id)
            return

        player.current_button = button
        if button == Button.START:
            player.on_start_press(timestamp)
        self.last_button = button
        await self.calculate_buttons()

    async def calculate_buttons(self):
        totals = {x:0 for x in ["up", "down", "left", "right", "select", "start", "a", "b"]}
        for i in self.players.keys():
            if self.players[i].current_button:
                totals[self.players[i].current_button] += 1
        max = 0
        max_item = None
        for i in totals.keys():
            if totals[i] > max:
                max_item = i
                max = totals[i]
        if self.last_button:
            if totals[self.last_button] == max:
                max_item = self.last_button
        if max_item != self.current_button:
            self.current_button = max_item
            await self.push_button()
        for i in self.players.keys():
            if self.players[i].current_button == self.current_button:
                self.players[i].light_settings = [Color.GREEN, Color.GREEN, Color.GREEN, Color.GREEN] 
            else:
                self.players[i].light_settings = [Color.RED, Color.RED, Color.RED, Color.RED] 
            await self.set_lights(self.players[i])

    async def push_button(self):
        self.press_counter += 1
        if self.press_counter > 100:
            self.press_counter = 0
            self.window = self.xdo.search_windows(winclass="mGBA".encode('ASCII'))[0]
            self.xdo.send_keysequence_window(self.window, 'Shift+F1'.encode('ASCII'), delay=0)
            print("Saved state")
            self.save_counter += 1
            if self.save_counter > 100:
                self.xdo.send_keysequence_window(self.window, 'Shift+F2'.encode('ASCII'), delay=0)
                self.save_counter = 0

        print("Pushing {}".format(self.current_button))
        for i in keycodes.keys():
            if i == self.current_button:
                self.xdo.send_keysequence_window_down(self.window, keycodes[i].encode('ASCII'), delay=0)
            else:
                self.xdo.send_keysequence_window_up(self.window, keycodes[i].encode('ASCII'), delay=0)

    async def kick(self, badge_id):
        """
        Removes a player from the game and informs the server that they have left.
        :param badge_id:
        :return:
        """

        self.publish('game.kick', game_id=GAME_ID, badge_id=badge_id)

    async def on_player_join(self, badge_id):
        """
        Called when a player joins the game, such as by entering a join sequence or entering a
        designated location.
        :param badge_id: The badge ID of the player who left
        :return: None
        """

        print("Badge #{} joined".format(badge_id))

        # Listen for button presses and releases
        press_sub = await self.subscribe(self.on_button_press, 'badge.' + str(badge_id) + '.button.press')
        release_sub = await self.subscribe(self.on_button_release, 'badge.' + str(badge_id) + '.button.release')

        # Add an entry to keep track of the player's game-state
        self.players[badge_id] = PlayerInfo(badge_id, subscriptions=[press_sub, release_sub])

        await self.set_lights(self.players[badge_id])

    async def on_player_leave(self, badge_id):
        """
        Called when a player leaves the game, such as by leaving a designated location.
        :param badge_id: The badge ID of the player who left
        :return: None
        """

        # Make sure we unsubscribe from all this badge's topics
        print("Badge #{} left".format(badge_id))
        await asyncio.gather(*(s.unsubscribe() for s in self.players[badge_id].subscriptions))
        self.players[badge_id].light_settings = [Color.OFF, Color.OFF, Color.OFF, Color.OFF]
        await self.set_lights(self.players[badge_id])
        del self.players[badge_id]

    async def onJoin(self, details):
        """
        WAMP calls this after successfully joining the realm.
        :param details: Provides information about
        :return: None
        """

        # Subscribe to all necessary things
        print("Now connected to the game server!")
        await self.subscribe(self.on_player_join, 'game.' + GAME_ID + '.player.join')
        await self.subscribe(self.on_player_leave, 'game.' + GAME_ID + '.player.leave')
        await self.subscribe(self.game_register, 'game.request_register')
        await self.game_register()

    def onDisconnect(self):
        """
        Called when the WAMP connection is disconnected
        :return: None
        """
        asyncio.get_event_loop().stop()


if __name__ == '__main__':
    runner = ApplicationRunner(
        WAMP_URL,
        WAMP_REALM,
    )
    runner.run(GameComponent, log_level='info')
