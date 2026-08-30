import { describe, expect, it } from "vitest";
import { isMobileWebDevice } from "./mobileDevice";

function mobileNavigator(
  userAgent: string,
  platform: string,
  maxTouchPoints: number,
  mobile?: boolean,
) {
  return { userAgent, platform, maxTouchPoints, userAgentData: { mobile } };
}

describe("isMobileWebDevice", () => {
  it("recognizes phone and tablet user agents at any viewport width", () => {
    expect(isMobileWebDevice(mobileNavigator("Mozilla/5.0 (iPhone)", "iPhone", 5))).toBe(true);
    expect(isMobileWebDevice(mobileNavigator("Mozilla/5.0 (Linux; Android 15)", "Linux", 5))).toBe(
      true,
    );
  });

  it("recognizes iPadOS desktop-site user agents without classifying a Mac", () => {
    const desktopSafari = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1";
    expect(isMobileWebDevice(mobileNavigator(desktopSafari, "MacIntel", 5))).toBe(true);
    expect(isMobileWebDevice(mobileNavigator(desktopSafari, "MacIntel", 0))).toBe(false);
  });

  it("does not classify a Windows touchscreen laptop as a mobile browser", () => {
    expect(isMobileWebDevice(mobileNavigator("Mozilla/5.0 (Windows NT 10.0)", "Win32", 10))).toBe(
      false,
    );
  });

  it("uses the browser-provided mobile hint when available", () => {
    expect(isMobileWebDevice(mobileNavigator("Desktop-like UA", "Linux", 0, true))).toBe(true);
  });
});
