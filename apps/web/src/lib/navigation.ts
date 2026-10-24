export interface NavItem {
  label: string;
  href: string;
  icon: string;
}

export const NAV_ITEMS: NavItem[] = [
  { label: "Home", href: "/", icon: "🏠" },
  { label: "Sniffer", href: "/sniffer", icon: "🐽" },
  { label: "OINK CORP", href: "/oink-corp", icon: "🧬" },
  { label: "Vitals", href: "/vitals", icon: "🩺" },
];
