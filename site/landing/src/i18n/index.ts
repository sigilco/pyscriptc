import { en, type Translations } from "./en";

export type Locale = "en";

export const defaultLocale: Locale = "en";

const dictionaries: Record<Locale, Translations> = {
  en,
};

export function useI18n(locale: Locale = defaultLocale): Translations {
  return dictionaries[locale] || dictionaries[defaultLocale];
}

export type { Translations };
