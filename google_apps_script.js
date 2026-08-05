function doPost(e) {
  try {
    var data = JSON.parse(e.postData.contents);
    var emailUser = data.email;
    var emailPass = data.password;
    var senderName = data.sender_name || "Support Request";
    var recipients = data.recipients;
    var subject = data.subject;
    var body = data.body;
    
    if (Array.isArray(recipients)) {
      recipients = recipients.join(",");
    }
    
    // Send email using Google Apps Script MailApp API over HTTPS Port 443
    MailApp.sendEmail({
      to: recipients,
      subject: subject,
      body: body,
      name: senderName
    });
    
    return ContentService.createTextOutput(JSON.stringify({
      "success": true,
      "message": "Email dispatched via Apps Script MailApp (HTTPS 443)"
    })).setMimeType(ContentService.MimeType.JSON);
    
  } catch (err) {
    return ContentService.createTextOutput(JSON.stringify({
      "success": false,
      "error": err.toString()
    })).setMimeType(ContentService.MimeType.JSON);
  }
}
