import os
import ssl
from celery import Celery
from app import create_app
from app.extentions import db
from app.services import user_service

celery = Celery(
    'abyflow',
    broker=os.getenv('REDIS_URL', 'redis://localhost:6379/0'),
    backend=None
)

# celery.conf.broker_use_ssl = {
#     'ssl_cert_reqs': ssl.CERT_NONE
# }

# celery.conf.redis_backend_use_ssl = {
#     'ssl_cert_reqs': ssl.CERT_NONE
# }

@celery.task
def process_payment_task(user_email):
    print("!!! CELERY TRAP: TASK RECEIVED !!!")
    flask_app = create_app()
    with flask_app.app_context():
        success, message = user_service.upgrade_user_to_pro(user_email)
        print(f"Celery Task Finished: {message}")
        return message
    
