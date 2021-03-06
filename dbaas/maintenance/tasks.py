from util import exec_remote_command
from datetime import datetime
from dbaas.celery import app
import models
import logging
from notification.models import TaskHistory
from util import get_worker_name
from util import build_context_script
from util import get_dict_lines
from django.core.exceptions import ObjectDoesNotExist
from registered_functions.functools import _get_function
from workflow.steps.util.dns import ChangeTTLTo5Minutes, ChangeTTLTo3Hours
from workflow.workflow import steps_for_instances

LOG = logging.getLogger(__name__)


@app.task(bind=True)
def execute_scheduled_maintenance(self, maintenance_id):
    LOG.debug("Maintenance id: {}".format(maintenance_id))
    maintenance = models.Maintenance.objects.get(id=maintenance_id)

    models.Maintenance.objects.filter(id=maintenance_id,
                                      ).update(status=maintenance.RUNNING, started_at=datetime.now())
    LOG.info("Maintenance {} is RUNNING".format(maintenance,))

    worker_name = get_worker_name()
    task_history = TaskHistory.register(
        request=self.request, worker_name=worker_name)

    LOG.info("id: %s | task: %s | kwargs: %s | args: %s" % (
        self.request.id, self.request.task, self.request.kwargs, str(self.request.args)))

    task_history.update_details(persist=True,
                                details="Executing Maintenance: {}".format(maintenance))

    for hm in models.HostMaintenance.objects.filter(maintenance=maintenance):
        main_output = {}
        hm.status = hm.RUNNING
        hm.started_at = datetime.now()
        hm.save()

        if hm.host is None:
            hm.status = hm.UNAVAILABLEHOST
            hm.finished_at = datetime.now()
            hm.save()
            continue

        host = hm.host
        update_task = "\nRunning Maintenance on {}".format(host)

        try:
            cloudstack_host_attributes = host.cs_host_attributes.get()
        except ObjectDoesNotExist as e:
            LOG.warn(
                "Host {} does not have cloudstack attrs...{}".format(hm.host, e))
            hm.status = hm.UNAVAILABLECSHOSTATTR
            hm.finished_at = datetime.now()
            hm.save()
            continue

        param_dict = {}
        for param in models.MaintenanceParameters.objects.filter(maintenance=maintenance):
            param_function = _get_function(param.function_name)
            param_dict[param.parameter_name] = param_function(host.id)

        main_script = build_context_script(param_dict, maintenance.main_script)
        exit_status = exec_remote_command(server=host.address,
                                          username=cloudstack_host_attributes.vm_user,
                                          password=cloudstack_host_attributes.vm_password,
                                          command=main_script, output=main_output)

        if exit_status == 0:
            hm.status = hm.SUCCESS
        else:

            if maintenance.rollback_script:
                rollback_output = {}
                hm.status = hm.ROLLBACK
                hm.save()

                rollback_script = build_context_script(
                    param_dict, maintenance.rollback_script)
                exit_status = exec_remote_command(server=host.address,
                                                  username=cloudstack_host_attributes.vm_user,
                                                  password=cloudstack_host_attributes.vm_password,
                                                  command=rollback_script, output=rollback_output)

                if exit_status == 0:
                    hm.status = hm.ROLLBACK_SUCCESS
                else:
                    hm.status = hm.ROLLBACK_ERROR

                hm.rollback_log = get_dict_lines(rollback_output)

            else:
                hm.status = hm.ERROR

        update_task += "...status: {}".format(hm.status)

        task_history.update_details(persist=True,
                                    details=update_task)

        hm.main_log = get_dict_lines(main_output)
        hm.finished_at = datetime.now()
        hm.save()

    models.Maintenance.objects.filter(id=maintenance_id,
                                      ).update(status=maintenance.FINISHED, finished_at=datetime.now())

    task_history.update_status_for(TaskHistory.STATUS_SUCCESS,
                                   details='Maintenance executed succesfully')

    LOG.info("Maintenance: {} has FINISHED".format(maintenance,))


def region_migration_prepare(infra):
    instance = infra.instances.first()
    ChangeTTLTo5Minutes(instance).do()


def region_migration_finish(infra):
    instance = infra.instances.first()
    ChangeTTLTo3Hours(instance).do()


@app.task(bind=True)
def region_migration_start(self, infra, instances, since_step=None):
    steps = [{
        'Disable monitoring and alarms': (
            'workflow.steps.util.zabbix.DestroyAlarms',
            'workflow.steps.util.db_monitor.DisableMonitoring',
        )}, {
        'Stopping infra': (
            'workflow.steps.util.database.Stop',
            'workflow.steps.util.database.CheckIsDown',
        )}, {
        'Creating new virtual machine': (
            'workflow.steps.util.vm.MigrationCreateNewVM',
        )}, {
        'Creating new infra': (
            'workflow.steps.util.vm.MigrationWaitingBeReady',
            'workflow.steps.util.infra.MigrationCreateInstance',
            'workflow.steps.util.disk.MigrationCreateExport',
        )}, {
        'Configuring new infra': (
            'workflow.steps.util.plan.InitializationMigration',
            'workflow.steps.util.plan.ConfigureMigration',
        )}, {
        'Preparing new environment': (
            'workflow.steps.util.disk.AddDiskPermissionsOldest',
            'workflow.steps.util.disk.MountOldestExportMigration',
            'workflow.steps.util.disk.CopyDataBetweenExportsMigration',
            'workflow.steps.util.disk.FilePermissionsMigration',
            'workflow.steps.util.disk.UnmountNewerExportMigration',
            'workflow.steps.util.vm.ChangeInstanceHost',
            'workflow.steps.util.vm.UpdateOSDescription',
            'workflow.steps.util.infra.UpdateMigrateEnvironment',
            'workflow.steps.util.infra.UpdateMigratePlan',
        )}, {
        'Starting new infra': (
            'workflow.steps.util.database.Start',
            'workflow.steps.util.database.CheckIsUp',
        )}, {
        'Enabling access': (
            'workflow.steps.util.dns.ChangeEndpoint',
            'workflow.steps.util.acl.ReplicateAclsMigration',
        )}, {
        'Destroying old infra': (
            'workflow.steps.util.disk.DisableOldestExportMigration',
            'workflow.steps.util.disk.DiskUpdateHost',
            'workflow.steps.util.vm.RemoveHost',
        )}, {
        'Enabling monitoring and alarms': (
            'workflow.steps.util.db_monitor.EnableMonitoring',
            'workflow.steps.util.zabbix.CreateAlarms',
        )}, {
        'Restart replication': (
            'workflow.steps.util.database.SetSlavesMigration',
        )
    }]

    task = TaskHistory()
    task.task_id = datetime.now().strftime("%Y%m%d%H%M%S")
    task.task_name = "migrating_zone"
    task.task_status = TaskHistory.STATUS_RUNNING
    task.context = {'infra': infra, 'instances': instances}
    task.arguments = {'infra': infra, 'instances': instances}
    task.user = 'admin'
    task.save()

    if steps_for_instances(steps, instances, task, since_step=since_step):
        task.set_status_success('Region migrated with success')
    else:
        task.set_status_error('Could not migrate region')


@app.task(bind=True)
def create_database(
    self, name, plan, environment, team, project, description, task,
    subscribe_to_email_events=True, is_protected=False, user=None,
    retry_from=None
):
    task = TaskHistory.register(
        request=self.request, task_history=task, user=user,
        worker_name=get_worker_name()
    )

    from tasks_create_database import create_database
    create_database(
        name, plan, environment, team, project, description, task,
        subscribe_to_email_events, is_protected, user, retry_from
    )
