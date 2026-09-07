//! The board: a fixed-size grid of cells and the neighbourhood arithmetic around it.
//!
//! Wrapping is a property of the board rather than of the rule, because "what counts as a
//! neighbour" is a question about the world and not about what the world does next.

use std::fmt;

use crate::rules::Rule as LifeRule;

/// Cells outside the grid, for a board that does not wrap.
pub const OUTSIDE: Cell = Cell::Dead;

/// The eight offsets of a Moore neighbourhood, in reading order.
pub static MOORE: [(i32, i32); 8] = [
    (-1, -1), (0, -1), (1, -1),
    (-1, 0), (1, 0),
    (-1, 1), (0, 1), (1, 1),
];

/// One square. Two states, and no third: a board that can be "unknown" is a different exercise.
#[derive(Clone, Copy, PartialEq, Eq, Debug)]
pub enum Cell {
    /// Nothing here.
    Dead,
    /// Something here.
    Live,
}

impl Cell {
    /// 1 for a live cell, so a neighbour count is a sum rather than a filtered length.
    pub fn count(self) -> u8 {
        match self {
            Cell::Live => 1,
            Cell::Dead => 0,
        }
    }
}

/// How the edges behave.
#[derive(Clone, Copy, PartialEq, Eq, Debug)]
pub enum Edge {
    /// The grid is a torus: the left column neighbours the right.
    Wrap,
    /// Everything past the edge is [`OUTSIDE`].
    Clamp,
}

/// A grid of cells, row-major, with its width fixed at construction.
#[derive(Clone)]
pub struct Board {
    width: usize,
    height: usize,
    edge: Edge,
    cells: Vec<Cell>,
}

impl Board {
    /// An empty board. Panics on a zero dimension, which is a programming error and not a state
    /// the rest of this crate should have to carry a branch for.
    pub fn new(width: usize, height: usize, edge: Edge) -> Self {
        assert!(width > 0 && height > 0, "a board needs both dimensions");
        Self { width, height, edge, cells: vec![Cell::Dead; width * height] }
    }

    /// Read a board from `.` and `#`, one row per line. Rows shorter than the widest are padded.
    pub fn parse(text: &str, edge: Edge) -> Self {
        let rows: Vec<&str> = text.lines().filter(|line| !line.trim().is_empty()).collect();
        let width = rows.iter().map(|r| r.chars().count()).max().unwrap_or(1).max(1);
        let mut board = Board::new(width, rows.len().max(1), edge);
        for (y, row) in rows.iter().enumerate() {
            for (x, ch) in row.chars().enumerate() {
                if ch == '#' {
                    board.set(x as i32, y as i32, Cell::Live);
                }
            }
        }
        board
    }

    pub fn width(&self) -> usize {
        self.width
    }

    pub fn height(&self) -> usize {
        self.height
    }

    /// The cell at `(x, y)`, resolving the edge policy rather than returning an Option: every
    /// caller wants a cell, and half of them would unwrap to [`OUTSIDE`] anyway.
    pub fn get(&self, x: i32, y: i32) -> Cell {
        match self.index(x, y) {
            Some(i) => self.cells[i],
            None => OUTSIDE,
        }
    }

    pub fn set(&mut self, x: i32, y: i32, cell: Cell) {
        if let Some(i) = self.index(x, y) {
            self.cells[i] = cell;
        }
    }

    /// How many of the eight neighbours are alive.
    pub fn neighbours(&self, x: i32, y: i32) -> u8 {
        let mut total = 0;
        for (dx, dy) in MOORE.iter() {
            total += self.get(x + dx, y + dy).count();
        }
        total
    }

    /// The next board under `rule`. A generation is a whole new grid on purpose - updating in
    /// place would let a cell see a neighbour that has already moved on.
    pub fn step(&self, rule: &LifeRule) -> Board {
        let mut next = self.clone();
        for y in 0..self.height as i32 {
            for x in 0..self.width as i32 {
                let here = self.get(x, y);
                next.set(x, y, rule.apply(here, self.neighbours(x, y)));
            }
        }
        next
    }

    /// How many cells are alive.
    pub fn population(&self) -> usize {
        self.cells.iter().filter(|c| **c == Cell::Live).count()
    }

    fn index(&self, x: i32, y: i32) -> Option<usize> {
        let (w, h) = (self.width as i32, self.height as i32);
        let (x, y) = match self.edge {
            Edge::Wrap => (x.rem_euclid(w), y.rem_euclid(h)),
            Edge::Clamp if x < 0 || y < 0 || x >= w || y >= h => return None,
            Edge::Clamp => (x, y),
        };
        Some(y as usize * self.width + x as usize)
    }
}

impl fmt::Display for Board {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        for y in 0..self.height as i32 {
            for x in 0..self.width as i32 {
                f.write_str(if self.get(x, y) == Cell::Live { "#" } else { "." })?;
            }
            writeln!(f)?;
        }
        Ok(())
    }
}
