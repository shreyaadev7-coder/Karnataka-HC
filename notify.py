import os
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import List, Dict, Any

def send_summary(new_matches: List[Dict[str, Any]]):
    """
    Handles summary notifications. 
    If email environment variables are missing, it gracefully skips sending.
    """
    if not new_matches:
        print("No new matches found. Skipping notification.")
        return

    sender_email = os.getenv("NOTIFICATION_EMAIL_FROM")
    sender_password = os.getenv("NOTIFICATION_EMAIL_PASSWORD")
    recipient_email = os.getenv("NOTIFICATION_EMAIL_TO")
    smtp_server = os.getenv("SMTP_SERVER", "smtp.gmail.com")
    smtp_port = int(os.getenv("SMTP_PORT", "587"))

    if not all([sender_email, sender_password, recipient_email]):
        print("Notification skipped: Email environment variables not configured.")
        return

    subject = f"🚨 Karnataka HC Cause List: {len(new_matches)} New Match(es)"
    
    html_body = f"""
    <html>
      <body style="font-family: Arial, sans-serif; line-height: 1.6; color: #333;">
        <h2>Karnataka High Court Cause List Alert</h2>
        <p>Found <strong>{len(new_matches)}</strong> new case listing(s):</p>
        <table border="1" cellpadding="8" cellspacing="0" style="border-collapse: collapse; width: 100%;">
          <thead>
            <tr style="background-color: #f2f2f2;">
              <th>Court Hall & Bench</th>
              <th>Case Number</th>
              <th>Parties</th>
              <th>Matched Advocate</th>
            </tr>
          </thead>
          <tbody>
    """
    for m in new_matches:
        html_body += f"""
            <tr>
              <td>{m.get('Court Hall & Bench (Name of Judges)', '')}</td>
              <td><strong>{m.get('Case Number', '')}</strong></td>
              <td>{m.get('Petitioner V/s Respondent', '')}</td>
              <td>{m.get('Advocate Name & Variant Matched', '')}</td>
            </tr>
        """
    html_body += """
          </tbody>
        </table>
      </body>
    </html>
    """

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = sender_email
    msg["To"] = recipient_email
    msg.attach(MIMEText(html_body, "html"))

    try:
        with smtplib.SMTP(smtp_server, smtp_port) as server:
            server.starttls()
            server.login(sender_email, sender_password)
            server.sendmail(sender_email, recipient_email, msg.as_string())
        print(f"Notification email successfully sent to {recipient_email}")
    except Exception as e:
        print(f"Failed to send email notification: {e}")
