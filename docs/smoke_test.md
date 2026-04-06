# LeadClaw Smoke Test Guide

Short, repeatable manual test to verify the full lifecycle works end-to-end.
Run this after any deploy or major code change.

---

## 1. Submit a Request

1. Open `{APP_URL}/request` in a browser (use Incognito to simulate a customer)
2. Fill out the form: name, phone, service, address
3. Click **Submit Request**
4. ✅ Confirm: success page shows "Request Received!" with the customer's name

**Anti-spam check (optional):**
- Reload the form, immediately click Submit without filling it in → should fail or reload
- Fill a value into the hidden `_hp_website` field (browser dev tools) → submission should silently succeed with no record stored

---

## 2. Confirm Request Appears as Unseen in Dashboard

1. Sign in to the dashboard
2. ✅ Confirm: **Requests** tab badge (top nav) shows a count > 0
3. ✅ Confirm: **Today** tab shows a blue "X new service requests waiting" banner with a tap-to-review link
4. Navigate away and back — badge should persist until requests are viewed

---

## 3. Confirm Notification Failure Doesn't Break Submission

1. Remove `OWNER_NOTIFY_EMAIL` from env (or set to invalid SMTP)
2. Submit a new request via `/request`
3. ✅ Confirm: success page still shows correctly
4. ✅ Confirm: request appears in dashboard Requests tab
5. ✅ Confirm: no 500 error in server logs

---

## 4. Open Requests Tab

1. Click the **Requests** tab (📥)
2. ✅ Confirm: requests are sorted newest first
3. ✅ Confirm: unseen requests appear at the top or prominently
4. ✅ Confirm: "Mark all seen" button appears if there are unseen requests

---

## 5. Book the Request

1. Click **Book** on the test request
2. Enter a scheduled date (tomorrow or any future date)
3. Optionally select a time window
4. Click **Confirm Booking**
5. ✅ Confirm: request moves out of Unbooked view
6. ✅ Confirm: toast "Booked! 🎉" appears

---

## 6. Confirm Booking Message Generates / Copies

1. After booking, confirm a booking confirmation message was auto-copied to clipboard (toast or log)
2. Paste it somewhere to verify it includes: customer first name, service name, scheduled date
3. ✅ Confirm: message looks natural and correct

---

## 7. Mark Job Completed

1. Find the booked lead in the Pipeline or Reminders → Jobs Today section
2. Click **Mark Complete**
3. ✅ Confirm: status changes to "completed"
4. ✅ Confirm: "Send Invoice" button now appears

---

## 8. Send Invoice

1. Click **Send Invoice**
2. Enter an amount (or leave blank to use the quote)
3. Click **Record Invoice**
4. ✅ Confirm: status shows invoice sent
5. ✅ Confirm: "Mark Paid" button now appears

---

## 9. Mark Paid

1. Click **Mark Paid**
2. ✅ Confirm: status changes to "paid" (green)
3. ✅ Confirm: "Schedule Next Service" option appears
4. ✅ Confirm: lead appears in Closed Leads section (More tab)

---

## 10. Confirm Reminders Behave Correctly

1. Go to the **Reminders** tab
2. ✅ Confirm: Jobs Today section shows booked jobs scheduled for today
3. ✅ Confirm: Invoice Reminders shows overdue invoices (test: set `invoice_reminder_at` to today in DB)
4. ✅ Confirm: Recurring Service Due appears after marking paid with recurring enabled
5. ✅ Confirm: Reminders badge count updates when new items appear
6. Run `python -m leadclaw.scheduler` and ✅ confirm: digest output shows any unseen requests first

---

## Done

If all 10 steps pass without errors, the app is ready for pilot use.
