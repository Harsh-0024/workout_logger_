"""
Email service for sending verification codes and admin notifications.
"""
import requests
from flask_mail import Mail, Message
from flask import current_app
from typing import Optional
from utils.logger import logger


class EmailService:
    
    def __init__(self, mail: Mail):
        self.mail = mail

    def _send_message(self, subject: str, recipients: list[str], body: str, html: Optional[str] = None) -> bool:
        try:
            brevo_api_key = current_app.config.get('BREVO_API_KEY')
            if brevo_api_key:
                sender_email = current_app.config.get('BREVO_SENDER_EMAIL')
                sender_name = current_app.config.get('BREVO_SENDER_NAME', 'Workout Tracker')

                if not sender_email:
                    logger.error("Brevo email not sent: BREVO_SENDER_EMAIL is not set")
                    return False

                payload = {
                    'sender': {
                        'email': sender_email,
                        'name': sender_name,
                    },
                    'to': [{'email': r} for r in recipients],
                    'subject': subject,
                    'textContent': body,
                }
                if html:
                    payload['htmlContent'] = html

                resp = requests.post(
                    'https://api.brevo.com/v3/smtp/email',
                    headers={
                        'accept': 'application/json',
                        'content-type': 'application/json',
                        'api-key': brevo_api_key,
                    },
                    json=payload,
                    timeout=20,
                )

                if 200 <= resp.status_code < 300:
                    logger.info(f"Brevo email sent to {', '.join(recipients)}")
                    return True

                logger.error(
                    f"Brevo email failed (status={resp.status_code}): {resp.text}"
                )
                return False

            msg = Message(
                subject=subject,
                recipients=recipients,
                body=body,
                html=html,
            )
            self.mail.send(msg)
            logger.info(f"SMTP email sent to {', '.join(recipients)}")
            return True
        except Exception as e:
            logger.error(f"Failed to send email to {', '.join(recipients)}: {e}", exc_info=True)
            return False
    
    def send_verification_email(self, email: str, username: str, verification_code: str) -> bool:
        """
        Send verification code email to user.
        
        Args:
            email: User's email address
            username: User's username
            verification_code: 6-digit verification code
        
        Returns:
            True if email sent successfully, False otherwise
        """
        try:
            subject = "Verify Your Workout Tracker Account"
            
            body = f"""
Hello {username.title()},

Welcome to Workout Tracker! To complete your registration, please verify your email address.

Your verification code is: {verification_code}

This code will expire in 24 hours.

If you didn't create this account, please ignore this email.

Best regards,
Workout Tracker Team
            """
            
            html_body = f"""
<!DOCTYPE html>
<html>
<head>
    <style>
        body {{ font-family: Arial, sans-serif; line-height: 1.6; color: #333; }}
        .container {{ max-width: 600px; margin: 0 auto; padding: 20px; }}
        .header {{ background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); color: white; padding: 30px; text-align: center; border-radius: 10px 10px 0 0; }}
        .content {{ background: #f9f9f9; padding: 30px; border-radius: 0 0 10px 10px; }}
        .code-box {{ background: white; border: 2px dashed #667eea; padding: 20px; margin: 20px 0; text-align: center; border-radius: 5px; }}
        .code {{ font-size: 32px; font-weight: bold; color: #667eea; letter-spacing: 5px; }}
        .footer {{ text-align: center; margin-top: 20px; color: #666; font-size: 12px; }}
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>🏋️ Workout Tracker</h1>
            <p>Email Verification</p>
        </div>
        <div class="content">
            <h2>Hello {username.title()},</h2>
            <p>Welcome to Workout Tracker! To complete your registration, please verify your email address using the code below:</p>
            
            <div class="code-box">
                <div class="code">{verification_code}</div>
            </div>
            
            <p><strong>This code will expire in 24 hours.</strong></p>
            
            <p>If you didn't create this account, please ignore this email.</p>
            
            <p>Best regards,<br>Workout Tracker Team</p>
        </div>
        <div class="footer">
            <p>This is an automated message, please do not reply.</p>
        </div>
    </div>
</body>
</html>
            """
            
            return self._send_message(subject=subject, recipients=[email], body=body, html=html_body)
            
        except Exception as e:
            logger.error(f"Failed to send verification email to {email}: {e}", exc_info=True)
            return False
    
    def send_account_deletion_email(
        self, 
        email: str, 
        username: str, 
        admin_message: str,
        admin_username: str
    ) -> bool:
        """
        Send account deletion notification to user.
        
        Args:
            email: User's email address
            username: User's username
            admin_message: Custom message from admin explaining deletion reason
            admin_username: Username of the admin who deleted the account
        
        Returns:
            True if email sent successfully, False otherwise
        """
        try:
            subject = "Workout Tracker Account Deleted"
            
            body = f"""
Hello {username.title()},

Your Workout Tracker account has been deleted by an administrator.

Reason from Administrator ({admin_username}):
{admin_message}

If you believe this was done in error, please contact support.

Best regards,
Workout Tracker Team
            """
            
            html_body = f"""
<!DOCTYPE html>
<html>
<head>
    <style>
        body {{ font-family: Arial, sans-serif; line-height: 1.6; color: #333; }}
        .container {{ max-width: 600px; margin: 0 auto; padding: 20px; }}
        .header {{ background: linear-gradient(135deg, #f093fb 0%, #f5576c 100%); color: white; padding: 30px; text-align: center; border-radius: 10px 10px 0 0; }}
        .content {{ background: #f9f9f9; padding: 30px; border-radius: 0 0 10px 10px; }}
        .message-box {{ background: white; border-left: 4px solid #f5576c; padding: 15px; margin: 20px 0; border-radius: 5px; }}
        .footer {{ text-align: center; margin-top: 20px; color: #666; font-size: 12px; }}
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>🏋️ Workout Tracker</h1>
            <p>Account Deletion Notice</p>
        </div>
        <div class="content">
            <h2>Hello {username.title()},</h2>
            <p>Your Workout Tracker account has been deleted by an administrator.</p>
            
            <div class="message-box">
                <h3>Reason from Administrator ({admin_username}):</h3>
                <p>{admin_message}</p>
            </div>
            
            <p>If you believe this was done in error, please contact support.</p>
            
            <p>Best regards,<br>Workout Tracker Team</p>
        </div>
        <div class="footer">
            <p>This is an automated message, please do not reply.</p>
        </div>
    </div>
</body>
</html>
            """
            
            return self._send_message(subject=subject, recipients=[email], body=body, html=html_body)
            
        except Exception as e:
            logger.error(f"Failed to send deletion email to {email}: {e}", exc_info=True)
            return False
    
    def send_welcome_email(self, email: str, username: str) -> bool:
        """
        Send welcome email after successful verification.
        
        Args:
            email: User's email address
            username: User's username
        
        Returns:
            True if email sent successfully, False otherwise
        """
        try:
            subject = "Welcome to Workout Tracker!"
            
            body = f"""
Hello {username.title()},

Your email has been successfully verified! Welcome to Workout Tracker.

You can now log in and start tracking your workouts.

Features available:
- Log your workouts
- Track your progress
- View statistics and charts
- Export your data

Get started at your dashboard!

Best regards,
Workout Tracker Team
            """
            
            html_body = f"""
<!DOCTYPE html>
<html>
<head>
    <style>
        body {{ font-family: Arial, sans-serif; line-height: 1.6; color: #333; }}
        .container {{ max-width: 600px; margin: 0 auto; padding: 20px; }}
        .header {{ background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); color: white; padding: 30px; text-align: center; border-radius: 10px 10px 0 0; }}
        .content {{ background: #f9f9f9; padding: 30px; border-radius: 0 0 10px 10px; }}
        .features {{ background: white; padding: 20px; margin: 20px 0; border-radius: 5px; }}
        .feature {{ padding: 10px 0; border-bottom: 1px solid #eee; }}
        .feature:last-child {{ border-bottom: none; }}
        .footer {{ text-align: center; margin-top: 20px; color: #666; font-size: 12px; }}
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>🏋️ Workout Tracker</h1>
            <p>Welcome Aboard!</p>
        </div>
        <div class="content">
            <h2>Hello {username.title()},</h2>
            <p>Your email has been successfully verified! Welcome to Workout Tracker.</p>
            
            <div class="features">
                <h3>Features Available:</h3>
                <div class="feature">📝 Log your workouts</div>
                <div class="feature">📊 Track your progress</div>
                <div class="feature">📈 View statistics and charts</div>
                <div class="feature">💾 Export your data</div>
            </div>
            
            <p>You can now log in and start tracking your fitness journey!</p>
            
            <p>Best regards,<br>Workout Tracker Team</p>
        </div>
        <div class="footer">
            <p>This is an automated message, please do not reply.</p>
        </div>
    </div>
</body>
</html>
            """
            
            return self._send_message(subject=subject, recipients=[email], body=body, html=html_body)
            
        except Exception as e:
            logger.error(f"Failed to send welcome email to {email}: {e}", exc_info=True)
            return False
