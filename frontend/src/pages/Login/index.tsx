import { useEffect, useState, type FormEvent } from 'react';
import { useNavigate } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { useAuthStore } from '@/store/authStore';
import api from '@/services/api';

const DEMO_ACCOUNTS = [
  { key: 'admin', label: 'Admin', email: 'admin@crm.com', password: 'Admin123' },
  { key: 'manager', label: 'Manager', email: 'sarah@crm.com', password: 'Manager123' },
  { key: 'sales', label: 'Sales', email: 'ryan@crm.com', password: 'Sales123' },
];

export default function LoginPage() {
  const navigate = useNavigate();
  const { t } = useTranslation();
  const { login, isLoading, error, clearError } = useAuthStore();

  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [showPassword, setShowPassword] = useState(false);
  const [rememberMe, setRememberMe] = useState(false);
  const [localErrors, setLocalErrors] = useState<{ email?: string; password?: string }>({});
  const [gitSha, setGitSha] = useState<string | null>(null);

  // Show the deployed version; hide it if health fails or the sha is unknown
  useEffect(() => {
    let cancelled = false;
    api
      .get<{ git_sha?: string }>('/health')
      .then((res) => {
        const sha = res.data?.git_sha;
        if (!cancelled && sha && sha !== 'unknown') setGitSha(sha.slice(0, 7));
      })
      .catch(() => {});
    return () => {
      cancelled = true;
    };
  }, []);

  const validate = () => {
    const errors: { email?: string; password?: string } = {};
    if (!email) {
      errors.email = t('enterEmail');
    } else if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email)) {
      errors.email = t('invalidEmail');
    }
    if (!password) {
      errors.password = t('enterPassword');
    }
    setLocalErrors(errors);
    return Object.keys(errors).length === 0;
  };

  const fillDemo = (account: (typeof DEMO_ACCOUNTS)[number]) => {
    setEmail(account.email);
    setPassword(account.password);
    setLocalErrors({});
    clearError();
  };

  const handleSubmit = async (e: FormEvent) => {
    e.preventDefault();
    clearError();
    if (!validate()) return;

    try {
      await login({ email, password });
      if (rememberMe) {
        localStorage.setItem('remember_me', 'true');
      }
      navigate('/dashboard', { replace: true });
    } catch {
      // error is set in store
    }
  };

  return (
    <div className="min-h-screen bg-dark-bg flex items-center justify-center px-4">
      <div className="w-full max-w-md">
        {/* Logo */}
        <div className="text-center mb-8">
          <h1 className="text-4xl font-bold bg-gradient-to-r from-primary to-accent bg-clip-text text-transparent">
            CRM Pro
          </h1>
          <p className="mt-2 text-text-secondary">{t('loginSubtitle')}</p>
        </div>

        {/* Login Card */}
        <div className="card p-8">
          <h2 className="text-xl font-semibold text-text-primary mb-6">{t('loginTitle')}</h2>

          {/* Server error */}
          {error && (
            <div className="mb-4 p-3 rounded-lg bg-red-500/10 border border-red-500/30 text-red-400 text-sm">
              {error}
            </div>
          )}

          <form onSubmit={handleSubmit} className="space-y-5">
            {/* Email */}
            <div>
              <label className="block text-sm font-medium text-text-secondary mb-1.5">
                {t('email')}
              </label>
              <input
                type="email"
                className={`input ${localErrors.email ? 'border-red-500 focus:border-red-500 focus:ring-red-500' : ''}`}
                placeholder={t('emailPlaceholder')}
                value={email}
                onChange={(e) => {
                  setEmail(e.target.value);
                  if (localErrors.email) setLocalErrors((p) => ({ ...p, email: undefined }));
                }}
                autoComplete="email"
              />
              {localErrors.email && (
                <p className="mt-1 text-sm text-red-400">{localErrors.email}</p>
              )}
            </div>

            {/* Password */}
            <div>
              <label className="block text-sm font-medium text-text-secondary mb-1.5">
                {t('password')}
              </label>
              <div className="relative">
                <input
                  type={showPassword ? 'text' : 'password'}
                  className={`input pr-10 ${localErrors.password ? 'border-red-500 focus:border-red-500 focus:ring-red-500' : ''}`}
                  placeholder={t('passwordPlaceholder')}
                  value={password}
                  onChange={(e) => {
                    setPassword(e.target.value);
                    if (localErrors.password) setLocalErrors((p) => ({ ...p, password: undefined }));
                  }}
                  autoComplete="current-password"
                />
                <button
                  type="button"
                  className="absolute right-3 top-1/2 -translate-y-1/2 text-text-muted hover:text-text-secondary transition-colors"
                  onClick={() => setShowPassword(!showPassword)}
                >
                  {showPassword ? (
                    <svg xmlns="http://www.w3.org/2000/svg" className="h-5 w-5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                      <path d="M17.94 17.94A10.07 10.07 0 0 1 12 20c-7 0-11-8-11-8a18.45 18.45 0 0 1 5.06-5.94M9.9 4.24A9.12 9.12 0 0 1 12 4c7 0 11 8 11 8a18.5 18.5 0 0 1-2.16 3.19m-6.72-1.07a3 3 0 1 1-4.24-4.24" />
                      <line x1="1" y1="1" x2="23" y2="23" />
                    </svg>
                  ) : (
                    <svg xmlns="http://www.w3.org/2000/svg" className="h-5 w-5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                      <path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z" />
                      <circle cx="12" cy="12" r="3" />
                    </svg>
                  )}
                </button>
              </div>
              {localErrors.password && (
                <p className="mt-1 text-sm text-red-400">{localErrors.password}</p>
              )}
            </div>

            {/* Remember me */}
            <div className="flex items-center justify-between">
              <label className="flex items-center gap-2 cursor-pointer">
                <input
                  type="checkbox"
                  checked={rememberMe}
                  onChange={(e) => setRememberMe(e.target.checked)}
                  className="w-4 h-4 rounded border-dark-border bg-dark-bg text-primary focus:ring-primary focus:ring-offset-0"
                />
                <span className="text-sm text-text-secondary">{t('rememberMe')}</span>
              </label>
            </div>

            {/* Submit */}
            <button
              type="submit"
              disabled={isLoading}
              className="btn-primary w-full h-11 flex items-center justify-center"
            >
              {isLoading ? (
                <svg className="animate-spin h-5 w-5" viewBox="0 0 24 24">
                  <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" fill="none" />
                  <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
                </svg>
              ) : (
                t('login')
              )}
            </button>
          </form>
        </div>

        {/* Demo Accounts */}
        <div className="mt-6">
          <p className="text-xs text-text-muted mb-2 text-center">
            Demo Accounts (click to fill)
          </p>
          <div className="grid grid-cols-2 sm:grid-cols-3 gap-3">
            {DEMO_ACCOUNTS.map((account) => (
              <button
                key={account.key}
                type="button"
                onClick={() => fillDemo(account)}
                className="p-3 rounded-lg bg-dark-card border border-dark-border hover:border-primary transition-colors text-left"
              >
                <div className="text-sm font-medium text-text-primary">{account.label}</div>
                <div className="text-xs text-text-muted mt-0.5 break-all">{account.email}</div>
              </button>
            ))}
          </div>
        </div>

        {/* Footer hint */}
        <p className="text-center text-text-muted text-xs mt-4">
          CRM Pro v1.0 &copy; 2026
        </p>
        {gitSha && (
          <p className="text-center text-text-muted text-xs mt-1">版本 {gitSha}</p>
        )}
      </div>
    </div>
  );
}
