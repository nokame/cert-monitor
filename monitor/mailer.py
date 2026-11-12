import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText


class Mailer:
    def __init__(self, config):
        self.config = config

    def is_configured(self):
        return bool(self.config["smtp_host"] and self.config["mail_from"] and self.config["mail_to"])

    def send_alert(self, site, result, threshold):
        if not self.is_configured():
            return False
        days_left = result["days_left"]
        expiry = result["not_after"]
        subject = (
            f"[Certificat HTTPS] {site['host']} expire dans {days_left} jour(s) "
            f"(seuil J-{threshold})"
        )
        body = self._build_html(site, result, threshold)
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = self.config["mail_from"]
        msg["To"] = ", ".join(self.config["mail_to"])
        msg.attach(MIMEText(subject, "plain", "utf-8"))
        msg.attach(MIMEText(body, "html", "utf-8"))
        try:
            if self.config["smtp_use_tls"] and self.config["smtp_port"] == 465:
                server = smtplib.SMTP_SSL(self.config["smtp_host"], self.config["smtp_port"], timeout=15)
            else:
                server = smtplib.SMTP(self.config["smtp_host"], self.config["smtp_port"], timeout=15)
                if self.config["smtp_use_tls"]:
                    server.starttls()
            if self.config["smtp_user"]:
                server.login(self.config["smtp_user"], self.config["smtp_password"] or "")
            server.sendmail(self.config["mail_from"], self.config["mail_to"], msg.as_string())
            server.quit()
            return True
        except (smtplib.SMTPException, OSError):
            return False

    def _build_html(self, site, result, threshold):
        days_left = result["days_left"]
        expiry = result["not_after"]
        color = "#c0392b" if days_left <= self.config["critical_days"] else "#e67e22"
        return f"""
        <html><body style="font-family: Arial, sans-serif; color: #333;">
          <h2 style="color: {color};">Certificat HTTPS bientôt expiré</h2>
          <table cellpadding="6" style="border-collapse: collapse;">
            <tr><td><b>Site</b></td><td>{site['name']} ({site['host']}:{site['port']})</td></tr>
            <tr><td><b>Date d'expiration</b></td><td>{expiry}</td></tr>
            <tr><td><b>Jours restants</b></td><td style="color: {color}; font-weight: bold;">{days_left}</td></tr>
            <tr><td><b>Seuil d'alerte</b></td><td>J-{threshold}</td></tr>
          </table>
          <p>Pensez à renouveler ce certificat rapidement.</p>
          <p style="font-size: 12px; color: #888;">Dashboard : {self.config['base_url']}</p>
        </body></html>
        """
