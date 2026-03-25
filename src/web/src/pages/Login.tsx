import { useMsal } from '@azure/msal-react';

function Login() {
  const { instance } = useMsal();

  const handleLogin = () => {
    instance.loginRedirect({
      scopes: ['User.Read'],
    });
  };

  return (
    <div className="card">
      <h1>Agent Memory</h1>
      <p>Sign in to access your personalized AI agent with long-term memory.</p>
      <button className="btn-sign-in" onClick={handleLogin}>
        Sign in with Microsoft
      </button>
    </div>
  );
}

export default Login;
