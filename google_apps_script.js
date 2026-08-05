function doPost(e) {
  return handleRequest(e);
}

function doGet(e) {
  return handleRequest(e);
}

function handleRequest(e) {
  try {
    var data = {};
    if (e && e.postData && e.postData.contents) {
      data = JSON.parse(e.postData.contents);
    } else if (e && e.parameter) {
      data = e.parameter;
    }
    
    var recipients = data.recipients;
    var subject = data.subject;
    var body = data.body;
    var senderName = data.sender_name || "Support Request";
    
    if (!recipients || !subject || !body) {
      return ContentService.createTextOutput(JSON.stringify({
        "success": false,
        "error": "Missing required fields (recipients, subject, or body)"
      })).setMimeType(ContentService.MimeType.JSON);
    }
    
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
