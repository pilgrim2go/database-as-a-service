# -*- coding: utf-8 -*-
from time import sleep
from workflow.steps.util.base import BaseInstanceStep
from workflow.steps.redis.util import reset_sentinel


class Reset(BaseInstanceStep):

    def __unicode__(self):
        return "Resetting Sentinel..."

    def __init__(self, instance):
        super(Reset, self).__init__(instance)
        self.driver = self.instance.databaseinfra.get_driver()
        self.sentinel_instance = self.host.non_database_instance()

    def do(self):
        sleep(10)
        if self.sentinel_instance:
            reset_sentinel(
                self.host,
                self.sentinel_instance.address,
                self.sentinel_instance.port,
                self.sentinel_instance.databaseinfra.name
            )

    def undo(self):
        pass
