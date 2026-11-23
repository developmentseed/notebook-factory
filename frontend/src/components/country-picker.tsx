import { Select, Text } from '@radix-ui/themes';
import type { RunCountry } from '../lib/run';

/** Imported countries compatible with the template and its current accepted selection. */
interface CountryPickerProps {
  countries: RunCountry[];
  country: RunCountry | undefined;
  onChange: (code: string) => void;
}

/**
 * Choose an imported country using the same Radix menu as Explore.
 * The trigger opens the complete list without typing; Radix owns type-ahead, focus and keyboard
 * selection. Only an accepted choice changes the analysis area, and choosing never submits it.
 *
 * @param props - Available countries and the route-owned selection.
 * @param props.countries - Countries with template-compatible imported boundaries.
 * @param props.country - Currently selected country, if any.
 * @param props.onChange - Accepts a country's ISO3 code after menu selection.
 * @returns A labelled country menu with a consistent theme-provided chevron.
 */
export function CountryPicker({ countries, country, onChange }: CountryPickerProps) {
  return (
    <div className='run-country-field'>
      <Text as='label' htmlFor='run-country' size='2' weight='medium'>
        Country
      </Text>
      <Select.Root
        size='3'
        value={country?.iso3 || ''}
        onValueChange={onChange}
        disabled={!countries.length}
      >
        <Select.Trigger id='run-country' placeholder='Select a country…' />
        <Select.Content
          position='popper'
          color='gray'
          variant='soft'
          highContrast
          className='run-country-menu'
        >
          {countries.map((item) => (
            <Select.Item key={item.iso3} value={item.iso3}>
              {item.name}
            </Select.Item>
          ))}
        </Select.Content>
      </Select.Root>
    </div>
  );
}
