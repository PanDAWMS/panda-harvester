import threading

from pandaharvester.harvesterconfig import harvester_config
from pandaharvester.harvestercore import CoreUtils
from pandaharvester.harvestercore.DBProxy import DBProxy
from pandaharvester.harvestercore.WorkSpec import WorkSpec
from pandaharvester.harvestercore.PluginFactory import PluginFactory

# logger
_logger = CoreUtils.setupLogger()


# propagate important checkpoints to panda
class Monitor(threading.Thread):
    # constructor
    def __init__(self, queue_config_mapper, single_mode=False):
        threading.Thread.__init__(self)
        self.queueConfigMapper = queue_config_mapper
        self.dbProxy = DBProxy()
        self.singleMode = single_mode
        self.pluginFactory = PluginFactory()

    # main loop
    def run(self):
        lockedBy = 'monitor-{0}'.format(self.ident)
        while True:
            mainLog = CoreUtils.makeLogger(_logger, 'id={0}'.format(lockedBy))
            mainLog.debug('getting workers to monitor')
            workSpecsPerQueue = self.dbProxy.getWorkersToUpdate(harvester_config.monitor.maxWorkers,
                                                                harvester_config.monitor.checkInterval,
                                                                harvester_config.monitor.lockInterval,
                                                                lockedBy)
            mainLog.debug('got {0} queues'.format(len(workSpecsPerQueue)))
            # loop over all workers
            for queueName, workSpecsList in workSpecsPerQueue.iteritems():
                tmpQueLog = CoreUtils.makeLogger(_logger, 'queue={0}'.format(queueName))
                # check queue
                if not self.queueConfigMapper.hasQueue(queueName):
                    tmpQueLog.error('config not found')
                    continue
                # get queue
                queueConfig = self.queueConfigMapper.getQueue(queueName)
                # get plugins
                monCore = self.pluginFactory.getPlugin(queueConfig.monitor)
                messenger = self.pluginFactory.getPlugin(queueConfig.messenger)
                # check workers
                allWorkers = [item for sublist in workSpecsList for item in sublist]
                tmpQueLog.debug('checking {0} workers'.format(len(allWorkers)))
                tmpRetMap = self.checkWorkers(monCore, messenger, allWorkers, tmpQueLog)
                # loop over all worker chunks
                iWorker = 0
                for workSpecs in workSpecsList:
                    jobSpecs = None
                    filesToStageOut = {}
                    for workSpec in workSpecs:
                        tmpLog = CoreUtils.makeLogger(_logger, 'workID={0}'.format(workSpec.workerID))
                        tmpOut = tmpRetMap[workSpec.workerID]
                        newStatus = tmpOut['newStatus']
                        diagMessage = tmpOut['diagMessage']
                        workAttributes = tmpOut['workAttributes']
                        eventsToUpdate = tmpOut['eventsToUpdate']
                        filesToStageOut = tmpOut['filesToStageOut']
                        eventsRequestParams = tmpOut['eventsRequestParams']
                        tmpLog.debug('newStatus={0} diag={1}'.format(newStatus, diagMessage))
                        iWorker += 1
                        # check status
                        if newStatus not in WorkSpec.ST_LIST:
                            tmpLog.error('unknown status={0}'.format(newStatus))
                            continue
                        # update worker
                        workSpec.status = newStatus
                        workSpec.workAttributes = workAttributes
                        # request events
                        if eventsRequestParams != {}:
                            workSpec.eventsRequest = WorkSpec.EV_requestEvents
                            workSpec.eventsRequestParams = eventsRequestParams
                        # get associated jobs for the worker chunk
                        if workSpec.hasJob == 1 and jobSpecs is None:
                            jobSpecs = self.dbProxy.getJobsWithWorkerID(workSpec.workerID,
                                                                        lockedBy)
                    # update jobs and workers
                    if jobSpecs is not None:
                        tmpQueLog.debug('update {0} jobs with {1} workers'.format(len(jobSpecs), len(workSpecs)))
                        messenger.updateJobAttributesWithWorkers(queueConfig.mapType, jobSpecs, workSpecs,
                                                                 filesToStageOut, eventsToUpdate)
                    # update local database
                    self.dbProxy.updateJobsWorkers(jobSpecs, workSpecs, lockedBy)
                    # send ACK to workers for events
                    if eventsToUpdate != []:
                        for workSpec in workSpecs:
                            messenger.acknowledgeEvents(workSpec)
                tmpQueLog.debug('done')
            mainLog.debug('done')
            if self.singleMode:
                return
            # sleep
            CoreUtils.sleep(harvester_config.monitor.sleepTime)

    # wrapper for checkWorkers
    def checkWorkers(self, mon_core, messenger, all_workers, tmp_log):
        workersToCheck = []
        retMap = {}
        for workSpec in all_workers:
            eventsRequestParams = {}
            eventsToUpdate = []
            # job-level late binding
            if workSpec.hasJob == 0:
                # check if job is requested
                jobRequested = messenger.jobRequested(workSpec)
                if jobRequested:
                    # set ready when job is requested 
                    workStatus = WorkSpec.ST_ready
                else:
                    workStatus = workSpec.status
                workAttributes = None
                filesToStageOut = None
            else:
                workStatus = None
                workersToCheck.append(workSpec)
                # request events
                if workSpec.eventsRequest == WorkSpec.EV_useEvents:
                    eventsRequestParams = messenger.eventsRequested(workSpec)
                # update events
                if workSpec.eventsRequest in [WorkSpec.EV_useEvents, WorkSpec.EV_requestEvents]:
                    eventsToUpdate = messenger.eventsToUpdate(workSpec)
                # get work attributes and output files
                workAttributes = messenger.getWorkAttributes(workSpec)
                filesToStageOut = messenger.getFilesToStageOut(workSpec)
            # add
            retMap[workSpec.workerID] = {'newStatus': workStatus,
                                         'workAttributes': workAttributes,
                                         'filesToStageOut': filesToStageOut,
                                         'eventsRequestParams': eventsRequestParams,
                                         'eventsToUpdate': eventsToUpdate,
                                         'diagMessage': ''}
        # check workers
        tmpStat, tmpOut = mon_core.checkWorkers(workersToCheck)
        if not tmpStat:
            tmp_log.error('failed to check workers with {0}'.format(tmpOut))
        else:
            for workSpec, (newStatus, diagMessage) in zip(workersToCheck, tmpOut):
                workerID = workSpec.workerID
                if workerID in retMap:
                    retMap[workerID]['newStatus'] = newStatus
                    retMap[workerID]['diagMessage'] = diagMessage
        return retMap