from .celery_app import celery_app

if __name__ == "__main__":
    # The solo pool is portable to Windows; Linux deployments may configure prefork externally.
    celery_app.worker_main(["worker", "--loglevel=INFO", "--pool=solo"])
