import os
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
import mod as m

class Mod:
    description = """protonmail"""
    path = r'/root/mod/mod/orbit/protonmail'

    def forward(self, **kwargs):
        """Default entry point; dispatches to send when action='send'."""
        action = kwargs.get('action')
        if action == 'send':
            return self.send(
                to=kwargs.get('to'),
                subject=kwargs.get('subject', ''),
                body=kwargs.get('body', ''),
                from_addr=kwargs.get('from_addr'),
                cc=kwargs.get('cc'),
                bcc=kwargs.get('bcc'),
                html_body=kwargs.get('html_body'),
            )
        return self.info()

    def info(self):
        """Return module info."""
        return {
            'name': 'protonmail',
            'description': self.description,
            'path': self.path,
            'files': os.listdir(self.path),
        }

    def send(self, to, subject, body, from_addr=None, cc=None, bcc=None, html_body=None):
        """Send email via ProtonMail Bridge SMTP.

        Reads connection details from env vars:
          PROTONMAIL_SMTP_HOST (default 127.0.0.1)
          PROTONMAIL_SMTP_PORT (default 1025)
          PROTONMAIL_SMTP_USER
          PROTONMAIL_SMTP_PASS
        """
        to_list = [to] if isinstance(to, str) else (list(to) if to else [])
        if not to_list:
            raise ValueError("'to' is required")
        host = os.environ.get('PROTONMAIL_SMTP_HOST', '127.0.0.1')
        port = int(os.environ.get('PROTONMAIL_SMTP_PORT', '1025'))
        user = os.environ.get('PROTONMAIL_SMTP_USER')
        password = os.environ.get('PROTONMAIL_SMTP_PASS')
        if not user or not password:
            raise ValueError("PROTONMAIL_SMTP_USER and PROTONMAIL_SMTP_PASS must be set")
        sender = from_addr or user
        cc_list = [cc] if isinstance(cc, str) else (list(cc) if cc else [])
        bcc_list = [bcc] if isinstance(bcc, str) else (list(bcc) if bcc else [])
        if html_body:
            msg = MIMEMultipart('alternative')
            msg.attach(MIMEText(body, 'plain'))
            msg.attach(MIMEText(html_body, 'html'))
        else:
            msg = MIMEText(body)
        msg['Subject'] = subject
        msg['From'] = sender
        msg['To'] = ', '.join(to_list)
        if cc_list:
            msg['Cc'] = ', '.join(cc_list)
        recipients = to_list + cc_list + bcc_list
        with smtplib.SMTP(host, port) as conn:
            conn.starttls()
            conn.login(user, password)
            conn.sendmail(sender, recipients, msg.as_string())
        result = {'sent': True, 'to': to_list, 'cc': cc_list or None, 'bcc': bcc_list or None}
        if html_body:
            result['html'] = True
        return result

    def readme(self):
        """Return the project README."""
        for name in ['README.md', 'readme.md', 'README.rst', 'README']:
            p = os.path.join(self.path, name)
            if os.path.exists(p):
                return m.get_text(p)
        return None
