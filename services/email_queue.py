"""
Email queue service for reliable email delivery with retry logic.
"""
import json
import time
from datetime import datetime, timedelta
from typing import Dict, List, Optional
from pathlib import Path
from threading import Lock
from utils.logger import logger


class EmailQueue:
    """Simple file-based email queue with retry logic."""
    
    def __init__(self, queue_dir: str = "email_queue", max_retries: int = 3):
        self.queue_dir = Path(queue_dir)
        self.queue_dir.mkdir(exist_ok=True)
        self.max_retries = max_retries
        self._lock = Lock()
    
    def enqueue(self, email_type: str, **kwargs) -> str:
        """Add an email to the queue."""
        email_id = f"{int(time.time() * 1000)}_{email_type}"
        
        email_data = {
            'id': email_id,
            'type': email_type,
            'created_at': datetime.now().isoformat(),
            'retry_count': 0,
            'next_retry': datetime.now().isoformat(),
            'data': kwargs
        }
        
        with self._lock:
            queue_file = self.queue_dir / f"{email_id}.json"
            with open(queue_file, 'w') as f:
                json.dump(email_data, f, indent=2)
        
        logger.info(f"Email queued: {email_id}")
        return email_id
    
    def get_pending_emails(self) -> List[Dict]:
        """Get all emails ready for processing."""
        pending = []
        now = datetime.now()
        
        with self._lock:
            for queue_file in self.queue_dir.glob("*.json"):
                try:
                    with open(queue_file, 'r') as f:
                        email_data = json.load(f)
                    
                    next_retry = datetime.fromisoformat(email_data['next_retry'])
                    if now >= next_retry and email_data['retry_count'] < self.max_retries:
                        pending.append(email_data)
                        
                except Exception as e:
                    logger.error(f"Error reading queue file {queue_file}: {e}")
        
        return pending
    
    def mark_sent(self, email_id: str):
        """Mark an email as successfully sent and remove from queue."""
        with self._lock:
            queue_file = self.queue_dir / f"{email_id}.json"
            if queue_file.exists():
                queue_file.unlink()
                logger.info(f"Email sent and removed from queue: {email_id}")
    
    def mark_failed(self, email_id: str):
        """Mark an email as failed and schedule retry or remove if max retries reached."""
        with self._lock:
            queue_file = self.queue_dir / f"{email_id}.json"
            if not queue_file.exists():
                return
            
            try:
                with open(queue_file, 'r') as f:
                    email_data = json.load(f)
                
                email_data['retry_count'] += 1
                
                if email_data['retry_count'] >= self.max_retries:
                    # Max retries reached, remove from queue
                    queue_file.unlink()
                    logger.error(f"Email failed permanently after {self.max_retries} retries: {email_id}")
                else:
                    # Schedule retry with exponential backoff
                    retry_delay = min(300 * (2 ** email_data['retry_count']), 3600)  # Max 1 hour
                    next_retry = datetime.now() + timedelta(seconds=retry_delay)
                    email_data['next_retry'] = next_retry.isoformat()
                    
                    with open(queue_file, 'w') as f:
                        json.dump(email_data, f, indent=2)
                    
                    logger.warning(f"Email failed, retry {email_data['retry_count']}/{self.max_retries} scheduled for {next_retry}: {email_id}")
                    
            except Exception as e:
                logger.error(f"Error updating failed email {email_id}: {e}")
    
    def process_queue(self, email_service):
        """Process all pending emails in the queue."""
        pending_emails = self.get_pending_emails()
        
        for email_data in pending_emails:
            try:
                email_type = email_data['type']
                data = email_data['data']
                
                success = False
                
                if email_type == 'account_deletion':
                    success = email_service.send_account_deletion_email(
                        email=data['email'],
                        username=data['username'],
                        admin_message=data['admin_message'],
                        admin_username=data['admin_username']
                    )
                elif email_type == 'otp':
                    success = email_service.send_otp_email(
                        email=data['email'],
                        username=data['username'],
                        otp_code=data['otp_code'],
                        purpose=data.get('purpose', 'login')
                    )
                elif email_type == 'verification':
                    success = email_service.send_verification_email(
                        email=data['email'],
                        username=data['username'],
                        verification_code=data['verification_code']
                    )
                else:
                    logger.error(f"Unknown email type: {email_type}")
                    self.mark_failed(email_data['id'])
                    continue
                
                if success:
                    self.mark_sent(email_data['id'])
                else:
                    self.mark_failed(email_data['id'])
                    
            except Exception as e:
                logger.error(f"Error processing email {email_data['id']}: {e}")
                self.mark_failed(email_data['id'])
    
    def cleanup_old_files(self, max_age_days: int = 7):
        """Remove old queue files that are older than max_age_days."""
        cutoff = datetime.now() - timedelta(days=max_age_days)
        
        with self._lock:
            for queue_file in self.queue_dir.glob("*.json"):
                try:
                    file_time = datetime.fromtimestamp(queue_file.stat().st_mtime)
                    if file_time < cutoff:
                        queue_file.unlink()
                        logger.info(f"Cleaned up old queue file: {queue_file}")
                except Exception as e:
                    logger.error(f"Error cleaning up queue file {queue_file}: {e}")


# Global email queue instance
email_queue = EmailQueue()