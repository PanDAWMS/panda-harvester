import threading

from pandaharvester.harvesterconfig import harvester_config
from pandaharvester.harvestercore import CoreUtils
from pandaharvester.harvestercore.JobSpec import JobSpec
from pandaharvester.harvestercore.DBProxy import DBProxy
from pandaharvester.harvestercore.PluginFactory import PluginFactory

# logger
_logger = CoreUtils.setupLogger()


# class for stage-out
class Stager(threading.Thread):
    # constructor
    def __init__(self, queue_config_mapper, single_mode=False):
        threading.Thread.__init__(self)
        self.dbProxy = DBProxy()
        self.queueConfigMapper = queue_config_mapper
        self.singleMode = single_mode
        self.pluginFactory = PluginFactory()

    # main loop
    def run(self):
        lockedBy = 'stager-{0}'.format(self.ident)
        while True:
            mainLog = CoreUtils.makeLogger(_logger, 'id={0}'.format(lockedBy))
            mainLog.debug('try to get jobs to check')
            # get jobs to check preparation
            jobsToCheck = self.dbProxy.getJobsForStageOut(harvester_config.stager.maxJobsToCheck,
                                                          harvester_config.stager.triggerInterval,
                                                          harvester_config.stager.lockInterval,
                                                          lockedBy, 'transferring',
                                                          JobSpec.HO_hasTransfer)
            mainLog.debug('got {0} jobs to check'.format(len(jobsToCheck)))
            # loop over all jobs
            for jobSpec in jobsToCheck:
                tmpLog = CoreUtils.makeLogger(_logger, 'PandaID={0}'.format(jobSpec.PandaID))
                tmpLog.debug('start checking')
                # get queue
                if not self.queueConfigMapper.hasQueue(jobSpec.computingSite):
                    tmpLog.error('queue config for {0} not found'.format(jobSpec.computingSite))
                    continue
                queueConfig = self.queueConfigMapper.getQueue(jobSpec.computingSite)
                # get plugin
                stagerCore = self.pluginFactory.getPlugin(queueConfig.stager)
                if stagerCore is None:
                    # not found
                    tmpLog.error('plugin for {0} not found'.format(jobSpec.computingSite))
                    continue
                tmpStat, tmpStr = stagerCore.checkStatus(jobSpec)
                # succeeded
                if tmpStat is True:
                    # update job
                    newSubStatus = self.dbProxy.updateJobForStageOut(jobSpec)
                    tmpLog.debug('succeeded newSubStatus={0}'.format(newSubStatus))
                else:
                    # failed
                    tmpLog.debug('failed with {0}'.format(tmpStr))
            # get jobs to trigger stage-out
            jobsToTrigger = self.dbProxy.getJobsForStageOut(harvester_config.stager.maxJobsToTrigger,
                                                            harvester_config.stager.triggerInterval,
                                                            harvester_config.stager.lockInterval,
                                                            lockedBy, 'totransfer',
                                                            JobSpec.HO_hasOutput)
            mainLog.debug('got {0} jobs to trigger'.format(len(jobsToTrigger)))
            # loop over all jobs
            for jobSpec in jobsToTrigger:
                tmpLog = CoreUtils.makeLogger(_logger, 'PandaID={0}'.format(jobSpec.PandaID))
                tmpLog.debug('try to trigger stage-out')
                # get queue
                if not self.queueConfigMapper.hasQueue(jobSpec.computingSite):
                    tmpLog.error('queue config for {0} not found'.format(jobSpec.computingSite))
                    continue
                queueConfig = self.queueConfigMapper.getQueue(jobSpec.computingSite)
                # get plugin
                stagerCore = self.pluginFactory.getPlugin(queueConfig.stager)
                if stagerCore is None:
                    # not found
                    tmpLog.error('plugin for {0} not found'.format(jobSpec.computingSite))
                    continue
                # trigger preparation
                tmpStat, tmpStr = stagerCore.triggerStageOut(jobSpec)
                # succeeded
                if tmpStat is True:
                    # update job
                    jobSpec.allFilesTriggeredToStageOut()
                    newSubStatus = self.dbProxy.updateJobForStageOut(jobSpec)
                    tmpLog.debug('triggered newSubStatus={0}'.format(newSubStatus))
                else:
                    # failed
                    tmpLog.debug('failed to trigger with {0}'.format(tmpStr))
            # get jobs to zip output
            jobsToZip = self.dbProxy.getJobsForStageOut(harvester_config.stager.maxJobsToZip,
                                                        harvester_config.stager.triggerInterval,
                                                        harvester_config.stager.lockInterval,
                                                        lockedBy, 'totransfer',
                                                        JobSpec.HO_hasZipOutput)
            mainLog.debug('got {0} jobs to zip'.format(len(jobsToZip)))
            # loop over all jobs
            for jobSpec in jobsToZip:
                tmpLog = CoreUtils.makeLogger(_logger, 'PandaID={0}'.format(jobSpec.PandaID))
                tmpLog.debug('try to zip output')
                # get queue
                if not self.queueConfigMapper.hasQueue(jobSpec.computingSite):
                    tmpLog.error('queue config for {0} not found'.format(jobSpec.computingSite))
                    continue
                queueConfig = self.queueConfigMapper.getQueue(jobSpec.computingSite)
                # get plugin
                stagerCore = self.pluginFactory.getPlugin(queueConfig.stager)
                if stagerCore is None:
                    # not found
                    tmpLog.error('plugin for {0} not found'.format(jobSpec.computingSite))
                    continue
                # trigger preparation
                tmpStat, tmpStr = stagerCore.zipOutput(jobSpec)
                # succeeded
                if tmpStat is True:
                    # update job
                    jobSpec.allFilesZipped()
                    newSubStatus = self.dbProxy.updateJobForStageOut(jobSpec)
                    tmpLog.debug('zipped newSubStatus={0}'.format(newSubStatus))
                else:
                    # failed
                    tmpLog.debug('failed to zip with {0}'.format(tmpStr))
            mainLog.debug('done')
            if self.singleMode:
                return
            # sleep
            CoreUtils.sleep(harvester_config.stager.sleepTime)