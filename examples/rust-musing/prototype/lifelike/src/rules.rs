//! What a cell does next, given what it is and how many neighbours it has.
//!
//! A rule is data, not code: B3/S23 is Conway's Life, B36/S23 is HighLife, and neither needs its
//! own function. The whole of a rule is two bitmasks over neighbour counts 0..=8.

use crate::board::Cell;

/// The birth/survival rule a generation is computed under.
///
/// Bit `n` of each mask means "a count of n". Nine counts fit in a `u16` with room to spare, so
/// membership is a shift and a mask rather than a scan of a list.
#[derive(Clone, Copy, PartialEq, Eq, Debug)]
pub struct Rule {
    born: u16,
    survives: u16,
}

/// A named rule and the string it was parsed from, for a menu that has to print itself.
#[derive(Clone, Copy, Debug)]
pub struct Named {
    pub label: &'static str,
    pub notation: &'static str,
}

/// The rules this crate ships with. Adding one is adding a line.
pub static CATALOGUE: [Named; 3] = [
    Named { label: "Life", notation: "B3/S23" },
    Named { label: "HighLife", notation: "B36/S23" },
    Named { label: "Seeds", notation: "B2/S" },
];

/// Why a rule string was rejected.
#[derive(Clone, Copy, PartialEq, Eq, Debug)]
pub enum ParseError {
    /// No `/` between the birth and survival halves.
    MissingSlash,
    /// A half did not start with its letter, or held something that was not a digit.
    BadHalf,
    /// A digit outside 0..=8, which no neighbourhood can produce.
    CountOutOfRange,
}

impl Rule {
    /// Conway's Life, the rule everything else is compared against.
    pub const LIFE: Rule = Rule { born: 1 << 3, survives: (1 << 2) | (1 << 3) };

    /// Build a rule from two lists of neighbour counts.
    pub fn new(born: &[u32], survives: &[u32]) -> Self {
        Self { born: mask(born), survives: mask(survives) }
    }

    /// Parse `B3/S23` notation. Case-insensitive; the letters are required.
    pub fn parse(notation: &str) -> Result<Self, ParseError> {
        let (birth, survival) = notation.split_once('/').ok_or(ParseError::MissingSlash)?;
        Ok(Self { born: half(birth, 'b')?, survives: half(survival, 's')? })
    }

    /// What `cell` becomes when it has `neighbours` live neighbours.
    pub fn apply(&self, cell: Cell, neighbours: u8) -> Cell {
        let bit = 1u16 << neighbours.min(8);
        let mask = match cell {
            Cell::Live => self.survives,
            Cell::Dead => self.born,
        };
        if mask & bit != 0 { Cell::Live } else { Cell::Dead }
    }

    /// The notation this rule would parse from, so a round trip is testable.
    pub fn notation(&self) -> String {
        let mut out = String::from("B");
        write_counts(&mut out, self.born);
        out.push_str("/S");
        write_counts(&mut out, self.survives);
        out
    }
}

impl Default for Rule {
    fn default() -> Self {
        Rule::LIFE
    }
}

fn mask(counts: &[u32]) -> u16 {
    counts.iter().fold(0u16, |acc, n| acc | (1u16 << (*n).min(8)))
}

fn half(text: &str, letter: char) -> Result<u16, ParseError> {
    let trimmed = text.trim();
    let digits = match trimmed.chars().next() {
        Some(c) if c.eq_ignore_ascii_case(&letter) => &trimmed[c.len_utf8()..],
        _ => return Err(ParseError::BadHalf),
    };
    let mut bits = 0u16;
    for ch in digits.chars() {
        let n = ch.to_digit(10).ok_or(ParseError::BadHalf)?;
        if n > 8 {
            return Err(ParseError::CountOutOfRange);
        }
        bits |= 1u16 << n;
    }
    Ok(bits)
}

fn write_counts(out: &mut String, bits: u16) {
    for n in 0..=8u32 {
        if bits & (1u16 << n) != 0 {
            out.push(char::from_digit(n, 10).expect("0..=8 is a digit"));
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn life_round_trips_through_its_notation() {
        assert_eq!(Rule::LIFE.notation(), "B3/S23");
        assert_eq!(Rule::parse("B3/S23"), Ok(Rule::LIFE));
    }

    #[test]
    fn a_half_without_its_letter_is_rejected() {
        assert_eq!(Rule::parse("3/23"), Err(ParseError::BadHalf));
        assert_eq!(Rule::parse("B3S23"), Err(ParseError::MissingSlash));
    }

    #[test]
    fn seeds_never_survives() {
        let seeds = Rule::parse("B2/S").expect("B2/S parses");
        assert_eq!(seeds.apply(Cell::Live, 2), Cell::Dead);
        assert_eq!(seeds.apply(Cell::Dead, 2), Cell::Live);
    }
}
