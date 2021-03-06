# -*- coding: utf-8 -*-
import logging
from threading import Timer
import threading
from errbot.botplugin import BotPlugin
from config import CHATROOM_PRESENCE
from errbot.errBot import admin_only
from errbot.jabberbot import botcmd
from gittools import clone, get_heads_revisions, fetch_all_heads, history_since_rev, git_log, remove_repo
from errbot.utils import human_name_for_git_url

POLLING_TIME = 180

class GitBot(BotPlugin):
    git_connected = False
    ggl = threading.Lock()

    def program_next_poll(self):
        self.t = Timer(POLLING_TIME, self.git_poller)
        self.t.setDaemon(True) # so it is not locking on exit
        self.t.start()


    def git_poller(self):
        try:
            with self.ggl:
                logging.debug('Poll the git repos')
                history_msgs = {}

                for human_name in self.shelf:
                    initial_state = self.shelf[human_name]
                    initial_state_dict = dict(initial_state)
                    logging.debug('fetch all heads of %s... ' % human_name)
                    fetch_all_heads(human_name)
                    new_state_dict = {head: rev for head, rev in get_heads_revisions(human_name)}
                    history_msg = ''
                    new_stuff = False
                    for head in initial_state_dict:
                        if initial_state_dict[head] != new_state_dict[head]:
                            logging.debug('%s: %s -> %s' % (head, initial_state_dict[head].encode("hex"), new_state_dict[head].encode("hex")))
                            new_stuff = True

                    if new_stuff:
                        log = git_log(history_since_rev(human_name, initial_state))
                        for head in log:
                            if log[head]: # don't log the empty branches
                                history_msg += '  Branch ' + head + ':\n    '
                                history_msg += '\n    '.join(log[head]) + '\n'
                        history_msgs[human_name] = history_msg
                    logging.debug('Saving the shelf')
                    self.shelf[human_name] = [(head, sha) for head, sha in new_state_dict.items() if head in initial_state_dict]
                    self.shelf.sync()
                    logging.debug('Syncing the shelf')
                if history_msgs:
                    if CHATROOM_PRESENCE:
                        room = CHATROOM_PRESENCE[0]
                        self.send(room, '/me is about to give you the latest git repo news ...', message_type='groupchat')
                        for repo, changes in history_msgs.iteritems():
                            msg = ('%s:\n' % repo) + changes
                            logging.debug('Send:\n%s' % msg)
                            self.send(room, msg, message_type='groupchat')
            logging.debug('Program the next poll')
            self.program_next_poll()
        except Exception, e:
            logging.exception('poller exploded')

    def _git_follow_url(self, git_url, heads_to_follow):
        human_name = human_name_for_git_url(git_url)
        with self.ggl:
            if self.shelf.has_key(human_name):
                fetch_all_heads(human_name)
                current_entry = self.shelf[human_name]
            else:
                human_name = clone(git_url)
                current_entry = []

            current_entry_dict = dict(current_entry)
            current_entry = [pair for pair in get_heads_revisions(human_name) if pair[0] in heads_to_follow or pair[0] in current_entry_dict] if heads_to_follow else get_heads_revisions(human_name)
            self.shelf[human_name] = current_entry
            self.shelf.sync()

            return self.following(None, None)

    @botcmd
    def follow(self, mess, args):
        """ Follow the given git repository url and be notified when somebody commits something on it
        The first argument is the git url.
        The next optional arguments are the heads to follow.
        If no optional arguments are given, just follow all the heads

        You can alternatively put a name of a plugin or 'allplugins' to follow the changes of the installed r2 plugins.
        """
        admin_only(mess)
        args = args.strip().split(' ')
        if len(args) < 1:
            return 'You need at least a parameter'
        git_name = args[0]
        result = ''
        installed_plugin_repos = self.get_installed_plugin_repos()
        if git_name == 'allplugins':
            for url in [url for name, url in installed_plugin_repos.iteritems()]:
                result = self._git_follow_url(url, None) # follow everything
            return result
        elif git_name in installed_plugin_repos:
            git_name = installed_plugin_repos[git_name] # transform the symbolic name to the url

        heads_to_follow = args[1:] if len(args) > 1 else None
        return self._git_follow_url(git_name, heads_to_follow)

    @botcmd
    def unfollow(self, mess, args):
        """ Unfollow the given git repository url or specific heads
        The first argument is the url.
        The next optional arguments are the heads to unfollow.
        If no optional arguments are given, just unfollow the repo completely
        """
        admin_only(mess)
        args = args.strip().split(' ')
        if len(args) < 1:
            return 'You need a parameter'
        human_name = str(args[0])
        heads_to_unfollow = args[1:] if len(args) > 1 else None

        with self.ggl:
            if not self.shelf.has_key(human_name):
                return 'I cannot find %s repos' % human_name

            if heads_to_unfollow:
                self.shelf[human_name] = [(head, sha) for head, sha in self.shelf[human_name] if head not in heads_to_unfollow]
                self.shelf.sync()
                return 'Heads %s have been removed from %s' % (','.join(heads_to_unfollow), human_name) + '\n\n' + self.following(None, None)

            remove_repo(human_name)
            del(self.shelf[human_name])
            self.shelf.sync()
            return ('%s has been removed.' % human_name) + '\n\n' + self.following(None, None)


    @botcmd
    def following(self, mess, args):
        """ Just prints out which git repo the bot is following
        """
        if not self.shelf:
            return 'You have no entry, please use !follow to add some'
        return '\nYou are currently following those repos:\n' + (
            '\n'.join(['\n%s:\n%s' % (human_name, '\t\n'.join([pair[0] for pair in current_entry])) for (human_name, current_entry) in self.shelf.iteritems()]))

    def callback_connect(self):
        logging.info('Callback_connect')
        if not self.git_connected:
            self.git_connected = True
            logging.info('Start git poller')
            self.program_next_poll()
