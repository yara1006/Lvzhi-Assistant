module.exports = {
  apps: [{
    name: 'legal-api',
    script: 'uvicorn',
    args: 'app.main:app --host 0.0.0.0 --port 8000',
    interpreter: '/home/ubuntu/backend/.venv/bin/python',
    autorestart: true,
    watch: false,
    env: {
      PATH: '/home/ubuntu/backend/.venv/bin:/usr/local/bin:/usr/bin:/bin'
    }
  }]
};
