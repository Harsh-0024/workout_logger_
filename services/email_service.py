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
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <style>
        * {{ margin: 0; padding: 0; box-sizing: border-box; }}
        body {{ 
            font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
            background: linear-gradient(135deg, #0a0a0a 0%, #1a1a1a 100%);
            padding: 40px 20px;
            line-height: 1.6;
        }}
        .container {{ 
            max-width: 600px;
            margin: 0 auto;
            background: rgba(20, 20, 20, 0.95);
            border-radius: 16px;
            overflow: hidden;
            box-shadow: 0 20px 60px rgba(0, 0, 0, 0.5), 0 0 0 1px rgba(212, 175, 55, 0.1);
        }}
        .header {{ 
            background: linear-gradient(135deg, rgba(212, 175, 55, 0.1) 0%, rgba(212, 175, 55, 0.05) 100%);
            padding: 40px 30px;
            text-align: center;
            border-bottom: 1px solid rgba(212, 175, 55, 0.2);
        }}
        .header h1 {{
            color: #D4AF37;
            font-size: 28px;
            font-weight: 600;
            margin-bottom: 8px;
            text-shadow: 0 2px 10px rgba(212, 175, 55, 0.3);
        }}
        .header p {{
            color: rgba(212, 175, 55, 0.7);
            font-size: 14px;
            text-transform: uppercase;
            letter-spacing: 2px;
        }}
        .content {{ 
            padding: 40px 30px;
            color: rgba(255, 255, 255, 0.9);
        }}
        .content h2 {{
            color: #D4AF37;
            font-size: 22px;
            margin-bottom: 20px;
            font-weight: 500;
        }}
        .content p {{
            color: rgba(255, 255, 255, 0.7);
            margin-bottom: 16px;
            font-size: 15px;
        }}
        .code-box {{ 
            background: rgba(212, 175, 55, 0.05);
            border: 2px solid rgba(212, 175, 55, 0.3);
            padding: 30px;
            margin: 30px 0;
            text-align: center;
            border-radius: 12px;
            box-shadow: 0 4px 20px rgba(212, 175, 55, 0.1);
        }}
        .code {{ 
            font-size: 36px;
            font-weight: 700;
            color: #D4AF37;
            letter-spacing: 8px;
            text-shadow: 0 2px 10px rgba(212, 175, 55, 0.3);
            font-family: 'Courier New', monospace;
        }}
        .highlight {{
            color: #D4AF37;
            font-weight: 600;
        }}
        .footer {{ 
            text-align: center;
            padding: 30px;
            color: rgba(255, 255, 255, 0.4);
            font-size: 12px;
            border-top: 1px solid rgba(212, 175, 55, 0.1);
        }}
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>🏋️ WORKOUT TRACKER</h1>
            <p>Email Verification</p>
        </div>
        <div class="content">
            <h2>Hello {username.title()},</h2>
            <p>Welcome to <span class="highlight">Workout Tracker</span>! To complete your registration, please verify your email address using the code below:</p>
            
            <div class="code-box">
                <div class="code">{verification_code}</div>
            </div>
            
            <p><span class="highlight">This code will expire in 24 hours.</span></p>
            
            <p>If you didn't create this account, please ignore this email.</p>
            
            <p style="margin-top: 30px;">Best regards,<br><span class="highlight">Workout Tracker Team</span></p>
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
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <style>
        * {{ margin: 0; padding: 0; box-sizing: border-box; }}
        body {{ 
            font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
            background: linear-gradient(135deg, #0a0a0a 0%, #1a1a1a 100%);
            padding: 40px 20px;
            line-height: 1.6;
        }}
        .container {{ 
            max-width: 600px;
            margin: 0 auto;
            background: rgba(20, 20, 20, 0.95);
            border-radius: 16px;
            overflow: hidden;
            box-shadow: 0 20px 60px rgba(0, 0, 0, 0.5), 0 0 0 1px rgba(220, 38, 38, 0.2);
        }}
        .header {{ 
            background: linear-gradient(135deg, rgba(220, 38, 38, 0.15) 0%, rgba(220, 38, 38, 0.05) 100%);
            padding: 40px 30px;
            text-align: center;
            border-bottom: 1px solid rgba(220, 38, 38, 0.3);
        }}
        .header h1 {{
            color: #DC2626;
            font-size: 28px;
            font-weight: 600;
            margin-bottom: 8px;
            text-shadow: 0 2px 10px rgba(220, 38, 38, 0.3);
        }}
        .header p {{
            color: rgba(220, 38, 38, 0.7);
            font-size: 14px;
            text-transform: uppercase;
            letter-spacing: 2px;
        }}
        .content {{ 
            padding: 40px 30px;
            color: rgba(255, 255, 255, 0.9);
        }}
        .content h2 {{
            color: #D4AF37;
            font-size: 22px;
            margin-bottom: 20px;
            font-weight: 500;
        }}
        .content p {{
            color: rgba(255, 255, 255, 0.7);
            margin-bottom: 16px;
            font-size: 15px;
        }}
        .message-box {{ 
            background: rgba(220, 38, 38, 0.05);
            border-left: 4px solid #DC2626;
            padding: 20px;
            margin: 25px 0;
            border-radius: 8px;
            box-shadow: 0 4px 20px rgba(220, 38, 38, 0.1);
        }}
        .message-box h3 {{
            color: #DC2626;
            font-size: 16px;
            margin-bottom: 12px;
            font-weight: 600;
        }}
        .message-box p {{
            color: rgba(255, 255, 255, 0.8);
            font-style: italic;
        }}
        .highlight {{
            color: #D4AF37;
            font-weight: 600;
        }}
        .footer {{ 
            text-align: center;
            padding: 30px;
            color: rgba(255, 255, 255, 0.4);
            font-size: 12px;
            border-top: 1px solid rgba(212, 175, 55, 0.1);
        }}
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>🏋️ WORKOUT TRACKER</h1>
            <p>Account Deletion Notice</p>
        </div>
        <div class="content">
            <h2>Hello {username.title()},</h2>
            <p>Your <span class="highlight">Workout Tracker</span> account has been deleted by an administrator.</p>
            
            <div class="message-box">
                <h3>Reason from Administrator ({admin_username}):</h3>
                <p>{admin_message}</p>
            </div>
            
            <p>If you believe this was done in error, please contact support.</p>
            
            <p style="margin-top: 30px;">Best regards,<br><span class="highlight">Workout Tracker Team</span></p>
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
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <style>
        * {{ margin: 0; padding: 0; box-sizing: border-box; }}
        body {{ 
            font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
            background: linear-gradient(135deg, #0a0a0a 0%, #1a1a1a 100%);
            padding: 40px 20px;
            line-height: 1.6;
        }}
        .container {{ 
            max-width: 600px;
            margin: 0 auto;
            background: rgba(20, 20, 20, 0.95);
            border-radius: 16px;
            overflow: hidden;
            box-shadow: 0 20px 60px rgba(0, 0, 0, 0.5), 0 0 0 1px rgba(212, 175, 55, 0.1);
        }}
        .header {{ 
            background: linear-gradient(135deg, rgba(212, 175, 55, 0.1) 0%, rgba(212, 175, 55, 0.05) 100%);
            padding: 40px 30px;
            text-align: center;
            border-bottom: 1px solid rgba(212, 175, 55, 0.2);
        }}
        .header h1 {{
            color: #D4AF37;
            font-size: 28px;
            font-weight: 600;
            margin-bottom: 8px;
            text-shadow: 0 2px 10px rgba(212, 175, 55, 0.3);
        }}
        .header p {{
            color: rgba(212, 175, 55, 0.7);
            font-size: 14px;
            text-transform: uppercase;
            letter-spacing: 2px;
        }}
        .content {{ 
            padding: 40px 30px;
            color: rgba(255, 255, 255, 0.9);
        }}
        .content h2 {{
            color: #D4AF37;
            font-size: 22px;
            margin-bottom: 20px;
            font-weight: 500;
        }}
        .content p {{
            color: rgba(255, 255, 255, 0.7);
            margin-bottom: 16px;
            font-size: 15px;
        }}
        .features {{ 
            background: rgba(212, 175, 55, 0.05);
            border: 1px solid rgba(212, 175, 55, 0.2);
            padding: 25px;
            margin: 25px 0;
            border-radius: 12px;
        }}
        .features h3 {{
            color: #D4AF37;
            font-size: 18px;
            margin-bottom: 16px;
            font-weight: 600;
        }}
        .feature {{ 
            padding: 12px 0;
            color: rgba(255, 255, 255, 0.8);
            border-bottom: 1px solid rgba(212, 175, 55, 0.1);
            font-size: 15px;
        }}
        .feature:last-child {{ border-bottom: none; }}
        .highlight {{
            color: #D4AF37;
            font-weight: 600;
        }}
        .footer {{ 
            text-align: center;
            padding: 30px;
            color: rgba(255, 255, 255, 0.4);
            font-size: 12px;
            border-top: 1px solid rgba(212, 175, 55, 0.1);
        }}
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>🏋️ WORKOUT TRACKER</h1>
            <p>Welcome Aboard!</p>
        </div>
        <div class="content">
            <h2>Hello {username.title()},</h2>
            <p>Your email has been successfully verified! Welcome to <span class="highlight">Workout Tracker</span>.</p>
            
            <div class="features">
                <h3>Features Available:</h3>
                <div class="feature">📝 Log your workouts</div>
                <div class="feature">📊 Track your progress</div>
                <div class="feature">📈 View statistics and charts</div>
                <div class="feature">💾 Export your data</div>
            </div>
            
            <p>You can now log in and start tracking your fitness journey!</p>
            
            <p style="margin-top: 30px;">Best regards,<br><span class="highlight">Workout Tracker Team</span></p>
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
