<?php
// Postmark inbound webhook: forwards every email sent to @willthesystemcollapse.com to FORWARD_TO through the Postmark API,
// with Reply-To set to the original sender. Nothing is stored.
// Config lives outside the web root and outside git: ~/web/willthesystemcollapse.com/private/postmark.ini
//   SERVER_TOKEN, INBOUND_SECRET, FROM ("Will the System Collapse <inbox@willthesystemcollapse.com>"), FORWARD_TO
// Postmark calls https://postmark:INBOUND_SECRET@willthesystemcollapse.com/api/postmark-inbound.php

$cfg = @parse_ini_file(__DIR__ . '/../../private/postmark.ini');
if (!$cfg) {
    http_response_code(503); // not configured yet; Postmark retries
    exit;
}

$auth = ($_SERVER['PHP_AUTH_USER'] ?? '') . ':' . ($_SERVER['PHP_AUTH_PW'] ?? '');
if (!hash_equals('postmark:' . $cfg['INBOUND_SECRET'], $auth)) {
    http_response_code(401);
    exit;
}

$in = json_decode(file_get_contents('php://input'), true);
if (!is_array($in)) {
    http_response_code(400);
    exit;
}

$sender = $in['FromFull']['Email'] ?? ($in['From'] ?? '');
$header = 'From: ' . ($in['From'] ?? '') . "\nTo: " . ($in['To'] ?? '') . "\n\n";
$mail = [
    'From' => $cfg['FROM'],
    'To' => $cfg['FORWARD_TO'],
    'ReplyTo' => $sender,
    'Subject' => '[wtsc] ' . ($in['Subject'] ?? '(no subject)'),
    'TextBody' => $header . ($in['TextBody'] ?? ''),
    'MessageStream' => 'outbound',
];
if (!empty($in['HtmlBody'])) {
    $mail['HtmlBody'] = '<p style="color:#666">' . nl2br(htmlspecialchars(trim($header))) . '</p>' . $in['HtmlBody'];
}
if (!empty($in['Attachments'])) {
    $mail['Attachments'] = $in['Attachments'];
}

$ch = curl_init('https://api.postmarkapp.com/email');
curl_setopt_array($ch, [
    CURLOPT_POST => true,
    CURLOPT_RETURNTRANSFER => true,
    CURLOPT_TIMEOUT => 20,
    CURLOPT_HTTPHEADER => [
        'Accept: application/json',
        'Content-Type: application/json',
        'X-Postmark-Server-Token: ' . $cfg['SERVER_TOKEN'],
    ],
    CURLOPT_POSTFIELDS => json_encode($mail),
]);
curl_exec($ch);
$status = curl_getinfo($ch, CURLINFO_HTTP_CODE);
curl_close($ch);

// Any non-2xx makes Postmark retry the inbound delivery, so a failed forward is never lost silently.
http_response_code($status === 200 ? 200 : 502);
