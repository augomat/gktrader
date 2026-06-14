from gktrader.tasks.celery_app import celery_app


def test_jobs_module_is_imported() -> None:
    assert "gktrader.tasks.jobs.poll_sources" in celery_app.tasks


def test_jobs_use_gktrader_queue() -> None:
    assert celery_app.conf.task_default_queue == "gktrader"
    assert celery_app.conf.task_routes["gktrader.tasks.jobs.*"]["queue"] == "gktrader"
