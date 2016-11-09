import datetime
import threading

from pandaharvester.harvesterconfig import harvester_config
from pandaharvester.harvestercore import CoreUtils
from pandaharvester.harvestercore.WorkSpec import WorkSpec
from pandaharvester.harvestercore.DBProxy import DBProxy
from pandaharvester.harvestercore.PluginFactory import PluginFactory
from WorkMaker import WorkMaker

# logger
_logger = CoreUtils.setupLogger()


# class to submit workers
class Submitter(threading.Thread):
    # constructor
    def __init__(self, queue_config_mapper, single_mode=False):
        threading.Thread.__init__(self)
        self.queueConfigMapper = queue_config_mapper
        self.dbProxy = DBProxy()
        self.singleMode = single_mode
        self.workMaker = WorkMaker()
        self.pluginFactory = PluginFactory()

    # main loop
    def run(self):
        lockedBy = 'submitter-{0}'.format(self.ident)
        while True:
            mainLog = CoreUtils.makeLogger(_logger, 'id={0}'.format(lockedBy))
            mainLog.debug('getting queues to submit workers')
            # get queues to submit workers
            nWorkersPerQueue = self.dbProxy.getQueuesToSubmit(harvester_config.submitter.nQueues,
                                                              harvester_config.submitter.lookupTime)
            mainLog.debug('got {0} queues'.format(len(nWorkersPerQueue)))
            # loop over all queues
            for queueName, tmpVal in nWorkersPerQueue.iteritems():
                tmpLog = CoreUtils.makeLogger(_logger, 'queue={0}'.format(queueName))
                tmpLog.debug('start')
                nWorkers = tmpVal['nWorkers']
                nReady = tmpVal['nReady']
                # check queue
                if not self.queueConfigMapper.hasQueue(queueName):
                    tmpLog.error('config not found')
                    continue
                # get queue
                queueConfig = self.queueConfigMapper.getQueue(queueName)
                # actions based on mapping type
                if queueConfig.mapType == WorkSpec.MT_NoJob:
                    # workers without jobs
                    jobChunks = []
                    for i in range(nWorkers):
                        jobChunks.append([])
                elif queueConfig.mapType == WorkSpec.MT_OneToOne:
                    # one worker per one job
                    jobChunks = self.dbProxy.getJobChunksForWorkers(queueName,
                                                                    nWorkers, nReady, 1, None,
                                                                    queueConfig.useJobLateBinding,
                                                                    harvester_config.submitter.checkInterval,
                                                                    harvester_config.submitter.lockInterval,
                                                                    lockedBy)
                elif queueConfig.mapType == WorkSpec.MT_MultiJobs:
                    # one worker for multiple jobs
                    nJobsPerWorker = self.workMaker.getNumJobsPerWorker(queueConfig)
                    jobChunks = self.dbProxy.getJobChunksForWorkers(queueName,
                                                                    nWorkers, nReady, nJobsPerWorker, None,
                                                                    queueConfig.useJobLateBinding,
                                                                    harvester_config.submitter.checkInterval,
                                                                    harvester_config.submitter.lockInterval,
                                                                    lockedBy)
                elif queueConfig.mapType == WorkSpec.MT_MultiWorkers:
                    # multiple workers for one job
                    nWorkersPerJob = self.workMaker.getNumWorkersPerJob(queueConfig)
                    jobChunks = self.dbProxy.getJobChunksForWorkers(queueName,
                                                                    nWorkers, nReady, None, nWorkersPerJob,
                                                                    queueConfig.useJobLateBinding,
                                                                    harvester_config.submitter.checkInterval,
                                                                    harvester_config.submitter.lockInterval,
                                                                    lockedBy)
                else:
                    tmpLog.error('unknown mapType={0}'.format(queueConfig.mapType))
                    continue
                tmpLog.debug('got {0} job chunks'.format(len(jobChunks)))
                if len(jobChunks) == 0:
                    continue
                # make workers
                okChunks, ngChunks = self.workMaker.makeWorkers(jobChunks, queueConfig, nReady)
                tmpLog.debug('made {0} workers while {1} failed'.format(len(okChunks), len(ngChunks)))
                timeNow = datetime.datetime.utcnow()
                # NG
                for ngJobs in ngChunks:
                    for jobSpec in ngJobs:
                        jobSpec.status = 'failed'
                        jobSpec.subStatus = 'failedtomake'
                        jobSpec.stateChangeTime = timeNow
                        jobSpec.lockedBy = None
                        jobSpec.triggerPropagation()
                        self.dbProxy.updateJob(jobSpec, {'lockedBy': lockedBy,
                                                         'subStatus': 'prepared'})
                # OK
                workSpecList = []
                if len(okChunks) > 0:
                    for workSpec, okJobs in okChunks:
                        # has job
                        if queueConfig.useJobLateBinding and workSpec.workerID is None:
                            workSpec.hasJob = 0
                        else:
                            workSpec.hasJob = 1
                        # queue name
                        workSpec.computingSite = queueConfig.queueName
                        # set access point
                        workSpec.accessPoint = queueConfig.messenger['accessPoint']
                        # events
                        if len(okJobs) > 0 and (
                                'eventService' in okJobs[0].jobParams or 'cloneJob' in okJobs[0].jobParams):
                            workSpec.eventsRequest = WorkSpec.EV_useEvents
                        workSpecList.append(workSpec)
                if len(workSpecList) > 0:
                    # get plugin for submitter
                    submitterCore = self.pluginFactory.getPlugin(queueConfig.submitter)
                    if submitterCore is None:
                        # not found
                        tmpLog.error('submitter plugin for {0} not found'.format(jobSpec.computingSite))
                        continue
                    # get plugin for messenger
                    messenger = self.pluginFactory.getPlugin(queueConfig.messenger)
                    if messenger is None:
                        # not found
                        tmpLog.error('messenger plugin for {0} not found'.format(jobSpec.computingSite))
                        continue
                    # setup access points
                    messenger.setup_access_points(workSpecList)
                    # submit
                    workSpecList, tmpRetList, tmpStrList = self.submitWorkers(submitterCore, workSpecList)
                    for iWorker, (tmpRet, tmpStr) in enumerate(zip(tmpRetList, tmpStrList)):
                        workSpec, jobList = okChunks[iWorker]
                        # failed
                        if tmpRet is None:
                            continue
                        # succeeded
                        if queueConfig.useJobLateBinding and workSpec.hasJob == 1:
                            workSpec.status = WorkSpec.ST_running
                        else:
                            workSpec.status = WorkSpec.ST_submitted
                        workSpec.mapType = queueConfig.mapType
                        workSpec.submitTime = timeNow
                        workSpec.stateChangeTime = timeNow
                        workSpec.modificationTime = timeNow
                        # register worker
                        tmpStat = self.dbProxy.registerWorker(workSpec, jobList, lockedBy)
                        for jobSpec in jobList:
                            if tmpStat:
                                tmpLog.debug('submitted a workerID={0} for PandaID={1} with batchID={2}'.format(
                                    workSpec.workerID,
                                    jobSpec.PandaID,
                                    workSpec.batchID))
                            else:
                                tmpLog.error('failed to register a worker for PandaID={0} with batchID={1}'.format(
                                    jobSpec.PandaID,
                                    workSpec.batchID))
                        # feed jobs
                        if tmpStat and workSpec.hasJob == 1:
                            tmpStat = messenger.feedJobs(workSpec, jobList)
                            tmpLog.debug('sent jobs to workerID={0} with {1}'.format(workSpec.workerID, tmpStat))
            mainLog.debug('done')
            if self.singleMode:
                return
            # sleep
            CoreUtils.sleep(harvester_config.submitter.sleepTime)

    # wrapper for submitWorkers to skip ready workers
    def submitWorkers(self, submitter_core, workspec_list):
        retList = []
        strList = []
        newSpecList = []
        workersToSubmit = []
        for workSpec in workspec_list:
            if workSpec.status == WorkSpec.ST_ready:
                newSpecList.append(workSpec)
                retList.append(True)
                strList.append('')
            else:
                workersToSubmit.append(workSpec)
        tmpRetList = submitter_core.submitWorkers(workersToSubmit)
        for tmpRet, tmpStr in tmpRetList:
            retList.append(tmpRet)
            strList.append(tmpStr)
        newSpecList += workersToSubmit
        return newSpecList, retList, strList