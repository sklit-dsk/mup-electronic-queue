import { afterEach, describe, expect, it } from "vitest";
import { getIntlLocale, setLocale, translateText } from "./i18n";

afterEach(() => setLocale("ru"));

describe("localisation", () => {
  it("translates interface text to Montenegrin", () => {
    setLocale("me");
    expect(getIntlLocale()).toBe("sr-Latn-ME");
    expect(translateText("Запись подтверждена")).toBe("Rezervacija je potvrđena");
  });

  it("translates interface text to English", () => {
    setLocale("en");
    expect(getIntlLocale()).toBe("en-GB");
    expect(translateText("Живая очередь")).toBe("Live queue");
  });
});
