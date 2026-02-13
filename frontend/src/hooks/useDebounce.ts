import { useState, useRef, useCallback, useEffect } from 'react';

export function useDebounce(initialValue: string, delay: number) {
  const [displayValue, setDisplayValue] = useState(initialValue);
  const [debouncedValue, setDebouncedValue] = useState(initialValue);
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const latestRef = useRef(initialValue);

  const clearTimer = useCallback(() => {
    if (timerRef.current !== null) {
      clearTimeout(timerRef.current);
      timerRef.current = null;
    }
  }, []);

  const setDisplay = useCallback((val: string) => {
    latestRef.current = val;
    setDisplayValue(val);
    clearTimer();
    timerRef.current = setTimeout(() => {
      setDebouncedValue(val);
      timerRef.current = null;
    }, delay);
  }, [delay, clearTimer]);

  const flushNow = useCallback(() => {
    clearTimer();
    setDebouncedValue(latestRef.current);
  }, [clearTimer]);

  const reset = useCallback((val = '') => {
    clearTimer();
    latestRef.current = val;
    setDisplayValue(val);
    setDebouncedValue(val);
  }, [clearTimer]);

  useEffect(() => clearTimer, [clearTimer]);

  return { displayValue, debouncedValue, setDisplayValue: setDisplay, flushNow, reset };
}
