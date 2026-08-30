interface NavigatorUserAgentData {
  mobile?: boolean;
}

type MobileNavigator = Pick<Navigator, "maxTouchPoints" | "platform" | "userAgent"> & {
  userAgentData?: NavigatorUserAgentData;
};

/** Detect phone/tablet web browsers whose wide landscape viewport resembles desktop. */
export function isMobileWebDevice(
  nav: MobileNavigator | undefined = typeof navigator === "undefined" ? undefined : navigator,
): boolean {
  if (!nav) return false;
  if (nav.userAgentData?.mobile === true) return true;
  if (/Android|iPhone|iPad|iPod|IEMobile|Opera Mini/i.test(nav.userAgent)) return true;

  // iPadOS requests desktop sites with a Macintosh UA. Real Macs report zero
  // touch points, so this avoids classifying a MacBook with a trackpad as mobile.
  return nav.platform === "MacIntel" && nav.maxTouchPoints > 1;
}
