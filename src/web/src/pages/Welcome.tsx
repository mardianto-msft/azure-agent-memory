import { useMsal } from '@azure/msal-react';

function Welcome() {
  const { instance, accounts } = useMsal();
  const account = accounts[0];

  const initials = account?.name
    ?.split(' ')
    .map((n: string) => n[0])
    .join('')
    .toUpperCase() || '?';

  const handleLogout = () => {
    instance.logoutRedirect({ postLogoutRedirectUri: window.location.origin });
  };

  return (
    <div className="card">
      <div className="avatar">{initials}</div>
      <h1>Welcome back!</h1>
      <p className="user-info">{account?.name}</p>
      <p className="user-email">{account?.username}</p>
      <button className="btn-sign-out" onClick={handleLogout}>
        Sign out
      </button>
    </div>
  );
}

export default Welcome;
