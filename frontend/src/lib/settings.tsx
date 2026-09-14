import { createContext, useCallback, useContext, useMemo, useState, type ReactNode } from "react";

interface SettingsValue {
  devMode: boolean;
  setDevMode: (value: boolean) => void;
  adminToken: string;
  setAdminToken: (value: string) => void;
}

const SettingsContext = createContext<SettingsValue | null>(null);

function read(key: string, fallback: string): string {
  try {
    return localStorage.getItem(key) ?? fallback;
  } catch {
    return fallback;
  }
}

function write(key: string, value: string): void {
  try {
    localStorage.setItem(key, value);
  } catch {
    /* storage unavailable */
  }
}

export function SettingsProvider({ children }: { children: ReactNode }) {
  const [devMode, setDevModeState] = useState(() => {
    if (new URLSearchParams(window.location.search).get("dev") === "1") return true;
    return read("biznext.devMode", "0") === "1";
  });
  const [adminToken, setAdminTokenState] = useState(() => read("biznext.adminToken", ""));

  const setDevMode = useCallback((value: boolean) => {
    setDevModeState(value);
    write("biznext.devMode", value ? "1" : "0");
  }, []);
  const setAdminToken = useCallback((value: string) => {
    setAdminTokenState(value);
    write("biznext.adminToken", value);
  }, []);

  const value = useMemo(
    () => ({ devMode, setDevMode, adminToken, setAdminToken }),
    [devMode, setDevMode, adminToken, setAdminToken],
  );
  return <SettingsContext.Provider value={value}>{children}</SettingsContext.Provider>;
}

// eslint-disable-next-line react-refresh/only-export-components
export function useSettings(): SettingsValue {
  const value = useContext(SettingsContext);
  if (!value) throw new Error("useSettings must be used inside SettingsProvider");
  return value;
}
