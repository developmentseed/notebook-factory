import { StackIcon } from '@radix-ui/react-icons';
import cyclone from '../assets/ocha-icons/cyclone.svg';
import drought from '../assets/ocha-icons/drought.svg';
import earthquake from '../assets/ocha-icons/earthquake.svg';
import wildfire from '../assets/ocha-icons/fire.svg';
import flood from '../assets/ocha-icons/flood.svg';
import heatwave from '../assets/ocha-icons/heatwave.svg';
import landslide from '../assets/ocha-icons/landslide-mudslide.svg';
import tsunami from '../assets/ocha-icons/tsunami.svg';
import volcano from '../assets/ocha-icons/volcano.svg';
import type { EventHazard } from '../lib/api';

/** OCHA humanitarian icons (CC0) by the API's hazard key. */
const ICONS: Record<string, string> = {
  cyclone,
  drought,
  earthquake,
  flood,
  heatwave,
  landslide,
  tsunami,
  volcano,
  wildfire,
};

/**
 * Show each hazard tagged on an event's analyses as a coloured icon tile.
 * The colour follows the hazard key through Radix colour tokens. Every tile is named for assistive
 * technology and on hover, so neither colour nor icon is the only cue. Hazards that staff add later
 * get a neutral tile.
 *
 * @param props - The card's hazards.
 * @param props.hazards - Hazard keys and labels, in the order the API returns them.
 * @returns One tile per hazard, or a decorative neutral tile when none is tagged.
 */
export function HazardTiles({ hazards }: { hazards: EventHazard[] }) {
  if (!hazards.length)
    return (
      <div className='hazard-tiles' aria-hidden='true'>
        <span className='hazard-tile'>
          <StackIcon width='20' height='20' />
        </span>
      </div>
    );

  return (
    <ul className='hazard-tiles' aria-label='Hazards'>
      {hazards.map((hazard) => {
        const icon = ICONS[hazard.key];
        const mask = icon ? `url("${icon}")` : undefined;

        return (
          <li
            key={hazard.key}
            className='hazard-tile'
            data-hazard={hazard.key}
            title={hazard.label}
          >
            {mask ? (
              <span
                className='hazard-icon'
                style={{ maskImage: mask, WebkitMaskImage: mask }}
                aria-hidden='true'
              />
            ) : (
              <StackIcon width='20' height='20' />
            )}
            <span className='explore-sr-only'>{hazard.label}</span>
          </li>
        );
      })}
    </ul>
  );
}
