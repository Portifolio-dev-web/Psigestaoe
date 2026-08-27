# Auth Testing Playbook — PsiGestão

Two auth methods coexist: JWT (email/password) and Emergent Google OAuth.

## JWT email/password
```
curl -c cookies.txt -X POST http://localhost:8001/api/auth/login \
  -H "Content-Type: application/json" \
  -d '{"email":"monitor.informatica25@gmail.com","password":"Psico@2025"}'
curl -b cookies.txt http://localhost:8001/api/auth/me
```
Login returns {user, token} and sets access_token cookie. /me returns the user.

## Google OAuth (Emergent)
- Frontend redirects to https://auth.emergentagent.com/?redirect=<origin>/dashboard
- Returns to /dashboard#session_id=... → frontend POST /api/auth/session {session_id}
- Backend calls https://demobackend.emergentagent.com/auth/v1/env/oauth/session-data
- Sets session_token cookie (7 days) stored in user_sessions collection.

## Browser test for Google-gated pages (seed session)
```
mongosh --eval "
use('test_database');
var uid='test-user-'+Date.now(); var st='test_session_'+Date.now();
db.users.insertOne({user_id:uid,email:'t'+Date.now()+'@ex.com',name:'Test',auth_provider:'google',role:'psicologo',terms_accepted:true,created_at:new Date()});
db.user_sessions.insertOne({user_id:uid,session_token:st,expires_at:new Date(Date.now()+7*24*3600*1000),created_at:new Date()});
print('token:'+st);"
```
Set cookie session_token=<token> (secure, sameSite=None) then load the app.

## Protected endpoints
All under /api require auth cookie or Authorization: Bearer <token>.
- GET/POST /api/patients ; GET/PUT/DELETE /api/patients/{id}
- POST /api/patients/{id}/anonymize ; GET /api/patients/{id}/export?format=pdf|json
- GET/POST /api/patients/{id}/records ; PUT /api/records/{id}
- GET/POST/PUT/DELETE /api/sessions
- GET /api/dashboard/stats ; GET /api/audit
