//! `lifelike` - a cellular automaton small enough to read in one sitting.
//!
//! It exists to give the code map real Rust to point at: doc comments, attributes, trait impls,
//! enum variants, an inline test module and a loop label are all things the symbol index has to
//! get right, and a crate that had none of them would prove nothing.
//!
//! ```text
//! cargo run -- --rule B36/S23 --generations 12
//! ```

// A fixture carries a few items nothing calls - a catalogue, a constructor the demo does not
// reach for - because the code map has to cite something of every shape. That is the point of
// them, so the warning is switched off rather than answered by deleting them.
#![allow(dead_code)]

mod board;
mod rules;

use std::env;

use board::{Board, Edge};
use rules::Rule as LifeRule;

/// How many generations to run when nobody says.
const DEFAULT_GENERATIONS: u32 = 8;

/// The seed pattern: an R-pentomino, which is small and does not settle quickly.
const SEED: &str = "\
.##
##.
.#.
";

/// Everything the command line can say.
struct Options {
    rule: LifeRule,
    generations: u32,
    edge: Edge,
}

impl Options {
    /// Read `--rule`, `--generations` and `--clamp`. Anything unrecognised is ignored rather than
    /// fatal: this is a fixture, and a stray flag should not stop it printing.
    fn from_args() -> Self {
        let mut options = Options { rule: LifeRule::LIFE, generations: DEFAULT_GENERATIONS, edge: Edge::Wrap };
        let args: Vec<String> = env::args().skip(1).collect();
        let mut i = 0;
        'parse: while i < args.len() {
            match args[i].as_str() {
                "--rule" if i + 1 < args.len() => {
                    if let Ok(parsed) = LifeRule::parse(&args[i + 1]) {
                        options.rule = parsed;
                    }
                    i += 1;
                }
                "--generations" if i + 1 < args.len() => {
                    options.generations = args[i + 1].parse().unwrap_or(DEFAULT_GENERATIONS);
                    i += 1;
                }
                "--clamp" => options.edge = Edge::Clamp,
                "--" => break 'parse,
                _ => {}
            }
            i += 1;
        }
        options
    }
}

fn main() {
    let options = Options::from_args();
    let mut board = Board::parse(SEED, options.edge);
    println!("{} on a {}x{} board", options.rule.notation(), board.width(), board.height());
    for generation in 0..=options.generations {
        println!("--- generation {generation} ({} alive)", board.population());
        print!("{board}");
        board = board.step(&options.rule);
    }
}
