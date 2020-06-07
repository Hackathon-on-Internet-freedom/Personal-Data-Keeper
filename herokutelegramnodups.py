# -*- coding: utf-8 -*-
# @Author Michael Pavlov

import datetime
import time
import threading
import logging
from logging.handlers import RotatingFileHandler

class HerokuTgNoDups:

    def __init__(self, lifetime_seconds=1, gc_interval_seconds=.500, logger=None):
        self.sessions = {}
        self.lifetime_s = lifetime_seconds
        self.gc_inteval_seconds = gc_interval_seconds

        if logger is None:
            self.logger = logging.getLogger("HerokuTgNoDups")
            self.logger.setLevel(logging.INFO)
            fh = RotatingFileHandler("herokutgnodups.log", mode='a', encoding='utf-8', backupCount=5,
                                     maxBytes=1 * 1024 * 1024)
            formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
            fh.setFormatter(formatter)
            self.logger.addHandler(fh)
        else:
            self.logger = logger

    def in_cache(self, user_, command_):
        ts_ = datetime.datetime.now()
        key = str(user_) + str(command_)
        if self.sessions.get(key) is not None:
            # session exists, it means duplicate message received. Need to update timestamp
            self.sessions[key] = ts_
            self.logger.debug("HerokuTgNoDups.in_cache(): update session: "  + key + ", " + str(ts_) + "; Sessions: " + str(self.sessions))
            return True
        else:
            # if nothing found need to create new item
            self.sessions[key] = ts_
            self.logger.debug("HerokuTgNoDups.in_cache(): add session: " + key + ", " + str(ts_) + "; Sessions: " + str(self.sessions))
            return False

    def gc(self, ts_):
        try:
            for key, value in self.sessions.copy().items():
                if value + datetime.timedelta(seconds=self.lifetime_s) < ts_:
                    self.sessions.pop(key)
                    self.logger.debug("HerokuTgNoDups.gc(): remove session: " + key)
        except Exception as e:
            self.logger.error("HerokuTgNoDups.gc(): " + str(e).replace("\n","")  + "; Sessions: " + str(self.sessions))
            pass

    def run_gc(self):
        while True:
            current_ts = datetime.datetime.now()
            self.gc(current_ts)
            time.sleep(self.gc_inteval_seconds)

    def run(self):
        self.t_gc = threading.Thread(target=self.run_gc,daemon=True)
        self.t_gc.start()


if __name__ == '__main__':
    HD = HerokuTgNoDups()
    HD.run()
    print(HD.in_cache("user1","/start"))
