"""
Airflow version of update.ps1:  wait for the sales file -> load sales -> load plans -> export -> publish.

  Status: written as the next step after update.ps1 - render-tested, not yet deployed on an Airflow server.

  Daily at 08:00 (Bangkok). Each run works on the month of its data interval (the current month).
  Re-run / back-fill a past month: Trigger DAG w/ config {"month": "202605"} - one run per month.
  (Not `airflow dags backfill` over a date range: on a daily schedule that is one run per day,
  ~30 identical reloads of each month.)
  Re-running is safe: every load replaces its month (delete + insert, one transaction) and refuses an
  empty or partial file (load_sales.check_before_replace), so a re-run cannot duplicate or wipe data.

  Trigger options (Trigger DAG w/ config):  {"month": "202605"}  run for that month instead of the current one
                                             {"force": true}      reload sales files even if unchanged
                                             {"skip_plan": true}  sales only
                                             {"publish": false}   update the data file, do not push

  Failure handling
    - every script exits 1 when a file / tab fails -> the task fails
    - load tasks retry once (10 min) - enough for a dropped SQL Server connection; a guard failure
      (empty / partial file) fails the same way every time, so more retries only delay the alert
    - after the last retry: e-mail + on_failure_callback; export and publish do not run,
      so a broken load is never published (same rule as update.ps1)
    - max_active_runs=1: one run at a time writes to BMA_East

  Needs: Airflow on a machine that can reach the SQL Server (e.g. WSL / Docker with this repo mounted
  at BMA_HOME), the Python packages from requirements.txt, and git logged in for the publish step.
"""
import logging
import os
from datetime import timedelta

import pendulum
from airflow import DAG
from airflow.operators.bash import BashOperator
from airflow.operators.empty import EmptyOperator
from airflow.sensors.filesystem import FileSensor

BMA_HOME = os.environ.get("BMA_HOME", "/opt/bma-east")          # this repo
SALES_DIR = os.environ.get("BMA_SALES_DIR", "/data/7-11")        # folder with REPORT_CS_TRUE_DTAC_yyyymm.txt
ALERT_TO = os.environ.get("BMA_ALERT_TO", "data-team@example.com").split(",")

# Jinja, rendered per run
#   month from conf goes through | int, so only digits ever reach the bash command
MONTH = "{{ (dag_run.conf.get('month') | int) or data_interval_start.in_timezone('Asia/Bangkok').strftime('%Y%m') }}"
FORCE = "{{ '--force' if dag_run.conf.get('force') else '' }}"
SKIP_PLAN = "{{ 'yes' if dag_run.conf.get('skip_plan') else 'no' }}"
PUBLISH = "{{ 'no' if dag_run.conf.get('publish') == False else 'yes' }}"


def alert(context):
    """Last retry failed: log which task / month, so the alert channel shows where to look."""
    ti = context["task_instance"]
    month = (context["dag_run"].conf or {}).get("month") \
        or context["data_interval_start"].in_timezone("Asia/Bangkok").strftime("%Y%m")
    logging.error("BMA East update FAILED: task=%s month=%s try=%s log=%s",
                  ti.task_id, month, ti.try_number, ti.log_url)


default_args = {
    "owner": "bma-east",
    "retries": 2,
    "retry_delay": timedelta(minutes=10),
    "retry_exponential_backoff": True,
    "email": ALERT_TO,
    "email_on_failure": True,
    "email_on_retry": False,
    "on_failure_callback": alert,
}

with DAG(
    dag_id="bma_east_update",
    description="Load sales + plans into BMA_East, export dashboard data, publish",
    schedule="0 8 * * *",
    start_date=pendulum.datetime(2026, 1, 1, tz="Asia/Bangkok"),
    catchup=False,                      # past months only on request (trigger with {"month": "yyyymm"})
    max_active_runs=1,
    default_args=default_args,
    dagrun_timeout=timedelta(hours=2),
    tags=["bma-east", "etl"],
) as dag:

    wait_sales = FileSensor(
        task_id="wait_for_sales_file",
        filepath=SALES_DIR + "/REPORT_CS_TRUE_DTAC_" + MONTH + ".txt",
        poke_interval=15 * 60,
        timeout=6 * 60 * 60,
        mode="reschedule",              # frees the worker slot while waiting
    )

    load_sales = BashOperator(
        task_id="load_sales",
        bash_command=f"cd {BMA_HOME} && python etl/load_sales.py --folder {SALES_DIR} --only {MONTH} {FORCE}",
        execution_timeout=timedelta(minutes=45),
        retries=1,                      # see "Failure handling" above
    )

    load_plan = BashOperator(
        task_id="load_plan",
        bash_command=(f'if [ "{SKIP_PLAN}" = "yes" ]; then echo "sales only - plans skipped"; '
                      f"else cd {BMA_HOME} && python etl/load_plan.py --month {MONTH}; fi"),
        execution_timeout=timedelta(minutes=30),
        retries=1,
    )

    export = BashOperator(
        task_id="export_dashboard_data",
        bash_command=f"cd {BMA_HOME} && python etl/export_html.py",
        execution_timeout=timedelta(minutes=20),
    )

    publish = BashOperator(
        task_id="publish",
        bash_command=(f'if [ "{PUBLISH}" = "no" ]; then echo "not published (publish=false)"; exit 0; fi; '
                      f"cd {BMA_HOME} && git add data/dashboard-data.js && "
                      f"if git diff --cached --quiet -- data/dashboard-data.js; then echo 'Data unchanged - nothing to publish'; "
                      f"else git commit -q -m \"Data update {MONTH} ($(date '+%Y-%m-%d %H:%M'))\" && git push -q; fi"),
        retries=1,
    )

    done = EmptyOperator(task_id="done")

    wait_sales >> load_sales >> load_plan >> export >> publish >> done
