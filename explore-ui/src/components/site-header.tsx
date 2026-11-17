import { CodeIcon, GlobeIcon, InfoIcon } from "lucide-react"

import { Container } from "@/components/container"
import { Button } from "@/components/ui/button"
import {
  NavigationMenu,
  NavigationMenuItem,
  NavigationMenuLink,
  NavigationMenuList,
} from "@/components/ui/navigation-menu"

const NAV_ITEMS = [
  { label: "Explore", href: "/", icon: GlobeIcon },
  { label: "Build", href: "/build", icon: CodeIcon },
  { label: "About", href: "/about", icon: InfoIcon },
] as const

export function SiteHeader({ current = "/" }: { current?: string }) {
  return (
    <header className="border-b">
      <Container className="flex items-center justify-between gap-6 py-3">
        <div className="flex items-center gap-6">
          <a href="/" className="font-heading text-sm font-semibold">
            Montandon Notebooks
          </a>
          <NavigationMenu aria-label="Main">
            <NavigationMenuList className="gap-1">
              {NAV_ITEMS.map(({ label, href, icon: Icon }) => {
                const active = current === href
                return (
                  <NavigationMenuItem key={href}>
                    <NavigationMenuLink
                      href={href}
                      active={active}
                      aria-current={active ? "page" : undefined}
                      className="px-2.5 py-1.5 text-muted-foreground hover:text-foreground data-active:bg-muted data-active:font-semibold data-active:text-foreground"
                    >
                      <Icon aria-hidden="true" />
                      {label}
                    </NavigationMenuLink>
                  </NavigationMenuItem>
                )
              })}
            </NavigationMenuList>
          </NavigationMenu>
        </div>
        <Button>Log in</Button>
      </Container>
    </header>
  )
}
