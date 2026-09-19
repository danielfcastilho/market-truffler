export interface NavItem {
  label: string;
  href: string;
  icon: string;
}

export const NAV_ITEMS: NavItem[] = [
  { label: "Home", href: "/", icon: "🏠" },
  { label: "Sniffer", href: "/sniffer", icon: "🐽" },
  { label: "Truffler", href: "/truffler", icon: "🍄" },
  { label: "Warhog", href: "/warhog", icon: "🐗" },
  { label: "OINK CORP", href: "/oink-corp", icon: "🧬" },
  { label: "System", href: "/system", icon: "⚙️" },
];
