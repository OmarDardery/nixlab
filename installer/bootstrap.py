"""Initial wizard is a thin client of the existing guarded host transaction."""
import direct
import runtime as rt

PRINCIPAL = 'root-wizard-install'


def activate(manager=None, staged_source=None):
    from controller import Manager
    manager = manager or Manager()
    job_id = manager.review({}, principal=PRINCIPAL, host_role='bootstrap',
                            staged_source=staged_source)['id']
    print('Building initial onboarding; transaction ' + job_id, flush=True)
    direct.wait_for(manager, job_id, {'ready'})
    manager.apply(job_id)
    record = direct.wait_for(manager, job_id, {'awaiting-confirmation'})
    rt.pending_access(job_id, record)
    return record


def resume(manager, record):
    if record.get('principal') != PRINCIPAL or record.get('hostRole') != 'bootstrap':
        raise ValueError('Another transaction is active; finish it in its original entrypoint')
    return direct.resume(manager, record, principal=PRINCIPAL)
