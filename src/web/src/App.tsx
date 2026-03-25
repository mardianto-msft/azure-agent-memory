import { AuthenticatedTemplate, UnauthenticatedTemplate } from '@azure/msal-react';
import Login from './pages/Login';
import Chat from './pages/Chat';

function App() {
  return (
    <>
      <UnauthenticatedTemplate>
        <div className="app">
          <Login />
        </div>
      </UnauthenticatedTemplate>
      <AuthenticatedTemplate>
        <Chat />
      </AuthenticatedTemplate>
    </>
  );
}

export default App;
